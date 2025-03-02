import tempfile
import subprocess
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, List, Dict
from collections import defaultdict
import hashlib
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from ..utils.spec import Schema
from ..utils.colors import Colors
from ..agents.triage_agent import run_triage_agent, TriageAgentResult
from ..agents.patch_agent import run_patch_agent, PatchAgentResult
from .crash_repro import try_generate_reproducer, CrashResult
from .queue_repro import check_queue_safe
from ..agents.patch_endpoint_agent import EndpointFixAttempt
from ..agents.patch_sequence_agent import FilterResult

from memory_profiler import profile

TICK_ANALYSIS_LIMIT = 20

@dataclass
class Config:
    codebase: str
    rag: str
    fuzzer_build: str
    ext_build_script: str
    build_script: str
    schema: str
    config: str
    workdir: str
    bypass_validation: bool = False


@dataclass
class RunnerStage:
    workdir: str
    schema_data: str
    filter_data: List[str]
    _fuzzer_process: Optional[subprocess.Popen] = None
    _processed_crashes: set[Path] = None
    _processed_queue: set[Path] = None
    _crashes: List[CrashResult] = None
    _safe_queue: List[Path] = None

    _triage_results: Dict[str, TriageAgentResult] = None
    _patch_results: Dict[str, PatchAgentResult] = None

    def __init__(self, workdir: str, schema_data: str, filter_data: List[str]):
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.schema_data = schema_data
        self.filter_data = filter_data
        self._fuzzer_process = None
        self._processed_crashes = set()
        self._processed_queue = set()
        self._crashes = []
        self._safe_queue = []
        self._triage_results = {}
        self._patch_results = {}

        self.schema_path = self.workdir / "fuzzer.json"
        self.schema_path.write_text(self.schema_data)

        self.fuzzer_path = self.workdir / "fuzzer"

        self.filter_path = self.workdir / "filters"
        self.filter_path.mkdir(parents=True, exist_ok=True)
        for i in range(len(self.filter_data)):
            with open(self.filter_path / f"filter_{i}.json", "w") as f:
                f.write(self.filter_data[i])

        self.input_path = self.workdir / "input"
        self.input_path.mkdir(parents=True, exist_ok=True)

        self.output_path = self.workdir / "output"
        self.output_path.mkdir(parents=True, exist_ok=True)

        self.crashes_path = self.output_path / "crashes"
        self.crashes_path.mkdir(parents=True, exist_ok=True)

        self.queue_path = self.output_path / "queue"
        self.queue_path.mkdir(parents=True, exist_ok=True)

        self.crash_buckets_path = self.workdir / "crash_buckets"
        self.crash_buckets_path.mkdir(parents=True, exist_ok=True)

        self.safe_corpus_path = self.workdir / "safe_corpus"
        self.safe_corpus_path.mkdir(parents=True, exist_ok=True)

    def build_fuzzer(self, cfg: Config):
        subprocess.run([cfg.fuzzer_build, str(self.workdir / 'fuzzer')], check=True, capture_output=True)

    def start_fuzzer(self, bypass_validation: bool):
        print(f'{Colors.YELLOW}Starting fuzzer...{Colors.END}')
        if self._fuzzer_process is not None:
            raise RuntimeError("Fuzzer is already running")
        
        self._fuzzer_log = open(self.workdir / "fuzzer.log", "w")
        self._fuzzer_process = subprocess.Popen(
            [
                str(self.fuzzer_path),
                "-i", str(self.input_path),
                "-o", str(self.output_path),
                "-f", str(self.filter_path),
                "--bypass-validation" if bypass_validation else ""
            ],
            stdout=self._fuzzer_log,
            stderr=self._fuzzer_log
        )

    def stop_fuzzer(self):
        print(f'{Colors.YELLOW}Stopping fuzzer...{Colors.END}')
        if self._fuzzer_process is None:
            return
            
        self._fuzzer_process.terminate()
        try:
            self._fuzzer_process.wait(timeout=5)  # Wait up to 5 seconds for graceful termination
        except subprocess.TimeoutExpired:
            self._fuzzer_process.kill()  # Force kill if it doesn't terminate gracefully
            self._fuzzer_process.wait()
            
        self._fuzzer_process = None

    def update_triage(self, cfg: Config) -> bool:
        """Find and process any new crashes in the output directory"""
        if not self.crashes_path.exists():
            return []

        def process_crash(crash_file: Path, crash_tmpdir: Path) -> Optional[CrashResult]:    
            # Try to reproduce the crash
            res = try_generate_reproducer(Path(crash_tmpdir), str(self.fuzzer_path), cfg.ext_build_script, crash_file)
            return res

        found_new = False
        crash_files = list(self.crashes_path.glob("*"))

        crash_files = [f for f in crash_files if f not in self._processed_crashes and not f.name.startswith(".")]
        if len(crash_files) == 0:
            return False
        
        # Limit crashes to TICK_ANALYSIS_LIMIT
        if len(crash_files) > TICK_ANALYSIS_LIMIT:
            print(f'{Colors.YELLOW}Limiting to {TICK_ANALYSIS_LIMIT} crashes (out of {len(crash_files)}){Colors.END}')
            crash_files = crash_files[:TICK_ANALYSIS_LIMIT]
        
        # Use ThreadPoolExecutor to process crashes in parallel
        with ThreadPoolExecutor(max_workers=min(len(crash_files), 8)) as executor:
            future_to_crash = {}

            for crash_file in crash_files:
                crash_tmpdir = Path(self.workdir) / 'crash_tmpdir' / crash_file.name
                crash_tmpdir.mkdir(parents=True, exist_ok=True)
                future_to_crash[executor.submit(process_crash, crash_file, crash_tmpdir)] = crash_file
                self._processed_crashes.add(crash_file)
            
            total = len(crash_files)
            idx = 1
            for future in as_completed(future_to_crash):
                result = future.result()
                crash_file = future_to_crash[future]
                if result:
                    print(f"[{idx}/{total}][{Colors.GREEN}REPRO{Colors.END}] {result.crash_summary}")
                    self._crashes.append(result)
                    found_new = True
                else:
                    print(f"[{idx}/{total}][{Colors.RED}NO REPRO{Colors.END}] {crash_file.name}")
                idx += 1

        return found_new

    def update_queue(self, cfg: Config):
        """Rerun corpus inputs externally and verify they don't crash"""
        if not self.queue_path.exists():
            return

        def process_queue(queue_file: Path, queue_tmpdir: Path) -> Optional[CrashResult]:    
            # Try to reproduce the crash
            res = check_queue_safe(Path(queue_tmpdir), str(self.fuzzer_path), cfg.ext_build_script, queue_file)
            return res

        queue_files = list(self.queue_path.glob("*"))

        queue_files = [f for f in queue_files if f not in self._processed_queue and not f.name.startswith(".")]
        if len(queue_files) == 0:
            return
        
        # Limit to TICK_ANALYSIS_LIMIT crashes
        if len(queue_files) > TICK_ANALYSIS_LIMIT:
            print(f'{Colors.YELLOW}Limiting to {TICK_ANALYSIS_LIMIT} queue items (out of {len(queue_files)}){Colors.END}')
            queue_files = queue_files[:TICK_ANALYSIS_LIMIT]

        # Use ThreadPoolExecutor to process crashes in parallel
        with ThreadPoolExecutor(max_workers=min(len(queue_files), 8)) as executor:
            future_to_queue = {}

            for queue_file in queue_files:
                queue_tmpdir = Path(self.workdir) / 'queue_tmpdir' / queue_file.name
                queue_tmpdir.mkdir(parents=True, exist_ok=True)
                future_to_queue[executor.submit(process_queue, queue_file, queue_tmpdir)] = queue_file
                self._processed_queue.add(queue_file)
            
            total = len(queue_files)
            idx = 1
            for future in as_completed(future_to_queue):
                result = future.result()
                queue_file = future_to_queue[future]
                if result:
                    print(f"[{idx}/{total}][{Colors.GREEN}SAFE{Colors.END}] {queue_file.name}")
                    self._safe_queue.append(queue_file)
                else:
                    print(f"[{idx}/{total}][{Colors.RED}CRASH{Colors.END}] {queue_file.name}")
                idx += 1

        return

    def crashes_by_type(self) -> Dict[str, List[CrashResult]]:
        """Bucket crashes by crash type"""
        by_type = defaultdict(list)
        for crash in self._crashes:
            by_type[crash.crash_summary].append(crash)
        return by_type

    def save_crash_buckets(self):
        for crash_type, crashes in self.crashes_by_type().items():
            hsh = hashlib.sha256(crash_type.encode()).hexdigest()
            crash_folder = self.crash_buckets_path / hsh
            crash_folder.mkdir(parents=True, exist_ok=True)

            with open(crash_folder / "description.txt", "w") as f:
                f.write(crash_type)

            if crash_type in self._triage_results:
                with open(crash_folder / "triage.json", "w") as f:
                    f.write(self._triage_results[crash_type].model_dump_json())

            if crash_type in self._patch_results:
                with open(crash_folder / "patch.json", "w") as f:
                    f.write(self._patch_results[crash_type].model_dump_json())

            for crash in crashes:
                name = crash.name.replace('.', '_')
                with open(crash_folder / f"{name}.graph", "w") as f:
                    f.write(crash.minimized_graph)

                with open(crash_folder / f"{name}.cpp", "w") as f:
                    f.write(crash.external_code)

                with open(crash_folder / f"{name}.report", "w") as f:
                    f.write(crash.crash_report)

    def save_safe_corpus(self):
        for crash in self._safe_queue:
            shutil.copy(crash, self.safe_corpus_path / crash.name)

    def update_tick(self, cfg: Config) -> bool:
        # Check if fuzzer is dead and restart it
        if self._fuzzer_process.poll() is not None:
            print(f'{Colors.YELLOW}Fuzzer crashed, restarting...{Colors.END}')
            self.stop_fuzzer()
            self.start_fuzzer(cfg.bypass_validation)

        print(f'{Colors.YELLOW}TRIAGE{Colors.END}')
        found_new = self.update_triage(cfg)
        print(f'{Colors.YELLOW}QUEUE{Colors.END}')
        self.update_queue(cfg)
        print(f'{Colors.YELLOW}SAVE{Colors.END}')
        self.save_results()
        return found_new
    
    def save_results(self):
        self.save_crash_buckets()
        self.save_safe_corpus()

    def print_stats(self):
        print('-' * 40)
        by_type = self.crashes_by_type()
        counts = [(k, len(v)) for k, v in by_type.items()]
        counts.sort(key=lambda x: x[1], reverse=True)
        for k, v in counts:
            print(f'({Colors.GREEN}{v}{Colors.END}) {Colors.YELLOW}{k}{Colors.END}')
            if k in self._triage_results:
                print(f'  {Colors.GREEN}Triaged{Colors.END} {self._triage_results[k].explanation}')
            else:
                print(f'  {Colors.RED}Not triaged{Colors.END}')

            if k in self._patch_results:
                print(f'  {Colors.GREEN}Patched{Colors.END}')
                self._patch_results[k].pprint()
            else:
                print(f'  {Colors.RED}Not patched{Colors.END}')
        print('-' * 40)

    def attempt_triage(self, cfg: Config):
        by_type = self.crashes_by_type()
        counts = [(k, len(v)) for k, v in by_type.items()]
        counts.sort(key=lambda x: x[1], reverse=True)

        for k, v in counts:
            if k in self._triage_results:
                continue

            if v < 5:
                # Need at least 5 crashes to triage
                continue

            print(f'{Colors.YELLOW}Triage {k} ({v} crashes){Colors.END}')
            with tempfile.TemporaryDirectory() as tmpdir:
                crash_report = Path(tmpdir) / "crash.report"
                crash_report.write_text(by_type[k][0].crash_report)

                crash_cpp = Path(tmpdir) / "crash.cpp"
                crash_cpp.write_text(by_type[k][0].external_code)
                
                res = run_triage_agent(
                    str(crash_cpp),
                    str(crash_report),
                    cfg.codebase,
                    cfg.rag,
                    cfg.ext_build_script,
                )

                res.pprint()

                self._triage_results[k] = res
                self.save_results()

    def attempt_patch(self, cfg: Config):
        by_type = self.crashes_by_type()
        counts = [(k, len(v)) for k, v in by_type.items()]
        counts.sort(key=lambda x: x[1], reverse=True)

        for k, v in counts:
            if not k in self._triage_results:
                continue

            if k in self._patch_results:
                continue

            triage_analysis = self._triage_results[k]
            if triage_analysis.is_bug:
                continue

            print(f'{Colors.YELLOW}Patch {k} ({v} crashes){Colors.END}')

            with tempfile.TemporaryDirectory() as tmpdir:
                analysis_file = Path(tmpdir) / "analysis.json"
                analysis_file.write_text(triage_analysis.model_dump_json())

                examples_invalid = Path(tmpdir) / "examples_invalid"
                examples_invalid.mkdir(parents=True, exist_ok=True)

                for crash in by_type[k]:
                    with open(examples_invalid / crash.name, "w") as f:
                        f.write(crash.minimized_graph)

                testcase = Path(tmpdir) / by_type[k][0].name

                res = run_patch_agent(
                    str(self.schema_path),
                    analysis=str(analysis_file),
                    testcase=str(testcase),
                    fuzzer=str(self.fuzzer_path),
                    ex_valid=str(self.safe_corpus_path),
                    ex_invalid=str(examples_invalid),
                    build_script=cfg.build_script,
                    config=cfg.config,
                )

                res.pprint()
                if res.fix is None:
                    print(f'{Colors.RED}No patch found for {k}{Colors.END}')
                    continue

                self._patch_results[k] = res
                self.save_results()
class Runner:
    def __init__(self, config: Config):
        self.cfg = config

        self.workdir = Path(self.cfg.workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)

        self.stages = []

    def run(self):
        schema_data = open(self.cfg.schema).read()
        current_filters = []
        current_schema = Schema.model_validate_json(schema_data)

        stage_idx = 0
        while True:
            print(f'{Colors.YELLOW}Stage {stage_idx + 1}{Colors.END}')
            stage_idx += 1

            stage = RunnerStage(
                workdir=self.workdir / f"stage_{stage_idx}",
                schema_data=current_schema.model_dump_json(),
                filter_data=current_filters
            )

            self.stages.append(stage)

            stage.build_fuzzer(self.cfg)
            stage.start_fuzzer(self.cfg.bypass_validation)

            while True:
                stage.update_tick(self.cfg)
                
                try:
                    stage.attempt_triage(self.cfg)
                    stage.attempt_patch(self.cfg)
                except Exception as e:
                    # Probably LLM error, wait and try again
                    print(f'{Colors.RED}Error: {e}{Colors.END}')
                    time.sleep(10)

                stage.save_results()
                stage.print_stats()

                if len(stage._patch_results) > 0:
                    # Once we have patches, bail out and apply them
                    break

                time.sleep(1)

            stage.stop_fuzzer()

            print(f'{Colors.YELLOW}Applying patches...{Colors.END}')
            for k, v in stage._patch_results.items():
                print(f'{Colors.YELLOW}Applying patch for {k}...{Colors.END}')
                match v.fix:
                    case EndpointFixAttempt():
                        print(f'{Colors.YELLOW}Patching endpoints {k}...{Colors.END}')
                        for fix in v.fix.fixes:
                            print(f'{Colors.YELLOW}Patching endpoint {fix.endpoint_name}...{Colors.END}')
                            for endpoint in current_schema.endpoints:
                                if endpoint.name == fix.endpoint_name:
                                    endpoint.template = fix.new_template
                                    break
                    case FilterResult():
                        print(f'{Colors.YELLOW}Patching filter {k}...{Colors.END}')
                        current_filters.append(v.fix.filter_attempt.filter_function)
                    case _:
                        raise ValueError(f'Unknown patch type: {type(v.fix)}')

def main(args):
    runner = Runner(Config(
        schema=args.schema,
        config=args.config,
        fuzzer_build=args.fuzzer_build,
        ext_build_script=args.ext_build_script,
        build_script=args.build_script,
        codebase=args.codebase,
        rag=args.rag,
        workdir=args.workdir,
        bypass_validation=args.bypass_validation
    ))
    runner.run()


def register(subparsers):
    parser = subparsers.add_parser('run', help='Run the fuzzer')
    parser.add_argument('--schema', type=str, required=True)
    parser.add_argument('--config', type=str, required=True)
    parser.add_argument('--fuzzer-build', type=str, required=True, help='Script to build the fuzzer')
    parser.add_argument('--ext-build-script', type=str, required=True, help='Script to build external reproducers')
    parser.add_argument('--build-script', type=str, required=True, help='Script to build the fuzzer')
    parser.add_argument('--codebase', type=str, required=True, help='Path to the codebase')
    parser.add_argument('--rag', type=str, required=True, help='Path to the RAG')
    parser.add_argument('--workdir', type=str, required=True, help='Path to the working directory')
    parser.add_argument('--bypass-validation', action='store_true', help='Bypass validation of fuzzer schema')
    parser.set_defaults(func=main)
