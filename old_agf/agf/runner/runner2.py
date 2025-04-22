from agf.runner.crash import summary_hash
from ..utils.spec import Schema
from ..utils.info import ProjectInfo
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from .crash import Crash, ReproducedCrash, MinimizedCrash
import subprocess
import time
from ..build import build_full
from pathlib import Path
from ..utils.colors import Colors
from concurrent.futures import ThreadPoolExecutor
from .crash_repro import try_get_crash_result, try_generate_reproducer
from ..agents.minimizer_agent import run_minimizer_agent
from datetime import datetime


TICK_ANALYSIS_LIMIT = 100

# Max reproducers to try per crash type
MAX_REPRODUCERS = 10

# Max minimized cases to try per crash type
MINIMIZED_ATTEMPTS = 3

# Number of crashes to save on disk
CRASHES_ON_DISK = 100


class Config(BaseModel):
    info: ProjectInfo
    model: str
    schema_data: str
    workdir: str
    bypass_validation: bool


class CrashTracker(BaseModel):
    found_at: datetime
    original: Crash
    reproduced: ReproducedCrash|bool|None = None
    minimized: MinimizedCrash|bool|None = None
 

class RunnerStage:
    def __init__(self, runner: 'Runner', schema_data: str, stage: int):
        self.runner = runner
        self.schema_data = schema_data
        self.stage = stage

        # Local data
        self._triaged_crashes = set()
        self._crashes_by_hash: Dict[str, List[CrashTracker]] = {}

        self._seen_crashes = set()
        self._seen_reproduced = set()
        self._seen_minimized = set()

        # Directories
        self._workdir = Path(self.runner.config.workdir) / f"stage_{self.stage}"
        self._workdir.mkdir(parents=True, exist_ok=True)
        self._input_path = self._workdir / 'input'
        self._input_path.mkdir(parents=True, exist_ok=True)
        self._output_path = self._workdir / 'output'
        self._output_path.mkdir(parents=True, exist_ok=True)
        self._filter_path = self._workdir / 'filter'
        self._filter_path.mkdir(parents=True, exist_ok=True)
        self._crashes_path = self._output_path / 'crashes'
        self._crashes_path.mkdir(parents=True, exist_ok=True)

        self._fuzzer_path = self._workdir / 'fuzzer'
        (self._workdir / 'fuzzer.json').write_text(self.schema_data)

        self._crash_bucket_path = self._workdir / 'crash_buckets'
        self._crash_bucket_path.mkdir(parents=True, exist_ok=True)
        self._fuzzer_process = None
        self._fuzzer_log = None

    def log(self, msg: str):
        print(f"[{Colors.GREEN}*{Colors.END}] {msg}")

    def build_fuzzer(self, cfg: Config):
        self.log("Building fuzzer...")
        build_full(self._fuzzer_path, cfg.info)

    def start_fuzzer(self, bypass_validation: bool):
        self.log("Starting fuzzer...")
        if self._fuzzer_process is not None:
            raise RuntimeError("Fuzzer is already running")
        
        self._fuzzer_log = open(self._workdir / "fuzzer.log", "w")
        self._fuzzer_process = subprocess.Popen(
            [
                str(self._fuzzer_path),
                "-i", str(self._input_path),
                "-o", str(self._output_path),
                "-f", str(self._filter_path),
                "--bypass-validation" if bypass_validation else ""
            ],
            stdout=self._fuzzer_log,
            stderr=self._fuzzer_log
        )

    def ensure_running(self, bypass_validation: bool):
        # Loop until we see the fuzzer get past the "Loading corpus" stage
        # libAFL will complain if the fuzzer crashes immediately and doesn't log any coverage
        self.log("Waiting for fuzzer to start...")
        retry_timer = 10
        while True:
            if self._fuzzer_process.poll() is not None:
                self.log("Fuzzer crashed, restarting...")
                self.stop_fuzzer()
                self.start_fuzzer(bypass_validation)

            log = open(self._workdir / "fuzzer.log", "rb").read()
            if b'UserStats' in log:
                self.log("Fuzzer started!")
                break

            retry_timer -= 1
            if retry_timer <= 0:
                self.log("Force restarting fuzzer...")
                self.stop_fuzzer()
                self.start_fuzzer(bypass_validation)
                retry_timer = 10

            time.sleep(1)

    def stop_fuzzer(self):
        self.log("Stopping fuzzer...")
        if self._fuzzer_process is None:
            return
            
        self._fuzzer_process.terminate()
        try:
            self._fuzzer_process.wait(timeout=5)  # Wait up to 5 seconds for graceful termination
        except subprocess.TimeoutExpired:
            self._fuzzer_process.kill()  # Force kill if it doesn't terminate gracefully
            self._fuzzer_process.wait()
            
        self._fuzzer_process = None

    def update_tick(self, cfg: Config) -> bool:
        # Check if fuzzer is dead and restart it
        if self._fuzzer_process.poll() is not None:
            self.log("Fuzzer crashed, restarting...")
            self.stop_fuzzer()
            self.start_fuzzer(cfg.bypass_validation)

        # 1. Try to triage new crashes
        self.update_triage()
        self.save_crashes()

        # 2. Try to generate reproducers for new crashes
        self.update_reproducers()
        self.save_crashes()

        # 3. Try to generate LLM-minimized cases
        self.update_minimized()
        self.save_crashes()

    def update_triage(self):
        """Find and process any new crashes in the output directory"""
        if not self._crashes_path.exists():
            return

        crashes = list(self._crashes_path.glob("*"))
        crashes = [f for f in crashes if f not in self._triaged_crashes and not f.name.startswith(".")]
        if len(crashes) == 0:
            return
        
        # Limit crashes to TICK_ANALYSIS_LIMIT
        total = len(crashes)
        if len(crashes) > TICK_ANALYSIS_LIMIT:
            crashes = crashes[:TICK_ANALYSIS_LIMIT]

        self.log(f"Processing {len(crashes)} crashes ({total} total)")

        def process_crash(crash_file: Path) -> Optional[Crash]:    
            return try_get_crash_result(str(self._fuzzer_path.absolute()), crash_file.absolute())

        # Process crashes in parallel
        with ThreadPoolExecutor(max_workers=10) as executor:
            results = zip(crashes, executor.map(process_crash, crashes))

        # Add results to buckets
        for crash_file, result in results:
            if result is not None:
                hsh = summary_hash(result.crash_summary)
                if not hsh in self._crashes_by_hash:
                    print(f"[{Colors.YELLOW}NEW{Colors.END}] {result.crash_summary}")
                    self._crashes_by_hash[hsh] = []
                    self._seen_crashes.add(hsh)
                
                self._crashes_by_hash[hsh].append(CrashTracker(original=result, found_at=crash_file.stat().st_ctime))

        # Update triaged crashes
        self._triaged_crashes.update(crashes)

    def update_reproducers(self):
        needs_repro: List[CrashTracker] = []

        for hsh, crashes in self._crashes_by_hash.items():
            for crash in crashes[:MAX_REPRODUCERS]:
                if crash.reproduced is None:
                    needs_repro.append(crash)

        if len(needs_repro) == 0:
            return

        self.log(f"Generating {len(needs_repro)} reproducers")

        def generate_reproducer(crash: Crash) -> Optional[ReproducedCrash]:
            return try_generate_reproducer(self.runner.config.info, crash)

        # Run reproducers in parallel
        with ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(generate_reproducer, [crash.original for crash in needs_repro]))

        for crash, result in zip(needs_repro, results):
            if result is None:
                # error case, no reproducer found
                crash.reproduced = False
            else:
                crash.reproduced = result

                # If this is the first reproducer for this crash, print it
                hsh = summary_hash(crash.original.crash_summary)
                if hsh not in self._seen_reproduced:
                    print(f"[{Colors.LIGHT_RED}REPRO{Colors.END}] {crash.original.crash_summary}")
                    self._seen_reproduced.add(hsh)

    def update_minimized(self):
        needs_minimize: List[CrashTracker] = []

        for hsh, crashes in self._crashes_by_hash.items():
            if any(crash.minimized is not None and crash.minimized is not False for crash in crashes):
                # Already minimized
                continue

            failed = 0
            for crash in crashes:
                if crash.minimized is False:
                    failed += 1

            if failed < MINIMIZED_ATTEMPTS:
                # Find a crash that hasn't been minimized yet
                for crash in crashes:
                    if crash.minimized is None and crash.reproduced is not None and crash.reproduced is not False:
                        needs_minimize.append(crash)
                        break

        if len(needs_minimize) == 0:
            return

        self.log(f"Minimizing {len(needs_minimize)} crashes")

        def run_minimizer(crash: CrashTracker) -> Optional[MinimizedCrash]:
            return run_minimizer_agent(
                self.runner.config.info,
                self.runner.config.model,
                str(crash.reproduced.external_code),
            )

        # Run minimizer agent
        with ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(run_minimizer, needs_minimize))

        for crash, result in zip(needs_minimize, results):
            if result is None:
                # error case, no minimized code found
                crash.minimized = False
            else:
                code, report = result
                crash.minimized = MinimizedCrash(
                    minimized_code=code,
                    crash_report=report,
                )

                # If this is the first minimized crash for this hash, print it
                hsh = summary_hash(crash.original.crash_summary)
                if hsh not in self._seen_minimized:
                    print(f"[{Colors.LIGHT_GREEN}MIN{Colors.END}] {crash.original.crash_summary}")
                    self._seen_minimized.add(hsh)

    def save_crashes(self):
        for hsh, crashes in self._crashes_by_hash.items():
            bucket_path = self._crash_bucket_path / hsh
            bucket_path.mkdir(parents=True, exist_ok=True)

            (bucket_path / 'info').write_text(crashes[0].original.crash_summary + '\n')

            crash_path = bucket_path / 'crashes'
            crash_path.mkdir(parents=True, exist_ok=True)

            for i, crash in enumerate(crashes[:CRASHES_ON_DISK]):
                (crash_path / f'crash_{i}.json').write_text(crash.model_dump_json(indent=2))
                (crash_path / f'crash_{i}.graph').write_text(crash.original.graph_data)

            reproduced_path = bucket_path / 'reproduced'
            reproduced_path.mkdir(parents=True, exist_ok=True)

            for i, crash in enumerate(crashes[:CRASHES_ON_DISK]):
                if crash.reproduced is None or crash.reproduced is False:
                    continue

                (reproduced_path / f'reproducer_{i}.cpp').write_text(crash.reproduced.external_code + '\n')
                (reproduced_path / f'reproducer_{i}.json').write_text(crash.reproduced.crash_report)

            minimized_path = bucket_path / 'minimized'
            minimized_path.mkdir(parents=True, exist_ok=True)

            for i, crash in enumerate(crashes[:CRASHES_ON_DISK]):
                if crash.minimized is None or crash.minimized is False:
                    continue

                (minimized_path / f'minimized_{i}.cpp').write_text(crash.minimized.minimized_code + '\n')
                (minimized_path / f'minimized_{i}.json').write_text(crash.minimized.crash_report)


class Runner:
    def __init__(self, config: Config):
        self.config = config

        self.stages = []

        self._info = Path(self.config.workdir) / 'info'
        self._info.mkdir(parents=True, exist_ok=True)

    def run(self):
        # Initial fuzz stage
        current_stage = 0
        stage = RunnerStage(self, self.config.schema_data, current_stage)

        while True:
            stage.build_fuzzer(self.config)
            stage.start_fuzzer(self.config.bypass_validation)
            stage.ensure_running(self.config.bypass_validation)

            while True:
                stage.update_tick(self.config)

                time.sleep(1)

def main(args):
    info = ProjectInfo.model_validate_json(open(args.info).read())
    runner = Runner(Config(
        info=info,
        model=args.model,
        schema_data=open(args.schema).read(),
        workdir=args.workdir,
        bypass_validation=args.bypass_validation
    ))
    runner.run()


def register(subparsers):
    parser = subparsers.add_parser('run', help='Run the fuzzer')
    parser.add_argument('--info', type=str, default='./info.json', help='Path to the project info file')
    parser.add_argument('--model', type=str, default='gpt-4o-mini', help='Model to use')
    parser.add_argument('--schema', type=str, required=True, help='Path to the initial fuzzer schema')
    parser.add_argument('--workdir', type=str, required=True, help='Path to the working directory')
    parser.add_argument('--bypass-validation', action='store_true', help='Bypass validation of fuzzer schema')
    parser.set_defaults(func=main)
