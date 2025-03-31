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
from .crash_repro import try_get_crash_result, CrashResult, ReproducedCrash, try_generate_reproducer
from .queue_repro import check_queue_safe
from ..agents.crash_analysis_agent import EndpointFixAttempt, EndpointFixAttempt
from ..utils.info import ProjectInfo
from ..agents.crash_analysis_agent import CrashAnalysisResult, run_crash_analysis_agent
from ..agents.minimizer_agent import run_minimizer_agent


TICK_ANALYSIS_LIMIT = 100

@dataclass
class Config:
    info: ProjectInfo
    model: str
    schema: str
    workdir: str
    bypass_validation: bool = False


@dataclass
class RunnerStage:
    workdir: str
    schema_data: str
    filter_data: List[str]
    _fuzzer_process: Optional[subprocess.Popen] = None

    _processed_crashes: set[Path] = None
    _crashes: List[CrashResult] = None
    _reproduced_crashes: List[ReproducedCrash] = None
    _crash_analysis: Dict[str, CrashAnalysisResult] = None
    _minimized_crashes: Dict[str, ReproducedCrash] = None

    def __init__(self, workdir: str, schema_data: str, filter_data: List[str]):
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.schema_data = schema_data
        self.filter_data = filter_data
        self._fuzzer_process = None
        self._processed_crashes = set()
        self._crashes = []
        self._reproduced_crashes = []
        self._crash_analysis = {}
        self._minimized_crashes = {}

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
        subprocess.run([cfg.info.build_script_full, str(self.workdir / 'fuzzer')], check=True, capture_output=True)

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

    def ensure_running(self, bypass_validation: bool):
        # Loop until we see the fuzzer get past the "Loading corpus" stage
        print(f'{Colors.YELLOW}Waiting for fuzzer to start...{Colors.END}')
        retry_timer = 10
        while True:
            if self._fuzzer_process.poll() is not None:
                print(f'{Colors.YELLOW}Fuzzer crashed, restarting...{Colors.END}')
                self.stop_fuzzer()
                self.start_fuzzer(bypass_validation)

            log = open(self.workdir / "fuzzer.log", "rb").read()
            if b'UserStats' in log:
                break

            print('.', end='', flush=True)
            retry_timer -= 1
            if retry_timer <= 0:
                print(f'{Colors.RED}Force restarting fuzzer...{Colors.END}')
                self.stop_fuzzer()
                self.start_fuzzer(bypass_validation)
                retry_timer = 10

            time.sleep(1)

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
            res = try_get_crash_result(str(self.fuzzer_path.absolute()), crash_file.absolute())
            if res is not None:
                return res
            return None

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
                    print(f"[{idx}/{total}][{Colors.GREEN}EXEC{Colors.END}] {crash_file.name} -- {result.crash_summary}")
                    self._crashes.append(result)
                    found_new = True
                else:
                    print(f"[{idx}/{total}][{Colors.RED}NO EXEC{Colors.END}] {crash_file.name}")
                idx += 1

        return found_new

    def update_queue(self, cfg: Config):
        """Rerun corpus inputs externally and verify they don't crash"""
        if not self.queue_path.exists():
            return

        def process_queue(queue_file: Path, queue_tmpdir: Path) -> Optional[CrashResult]:    
            # Try to reproduce the crash
            res = check_queue_safe(Path(queue_tmpdir), str(self.fuzzer_path), cfg.info.build_script_ext, queue_file)
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

            # if crash_type in self._triage_results:
            #     with open(crash_folder / "triage.json", "w") as f:
            #         f.write(self._triage_results[crash_type].model_dump_json())

            # if crash_type in self._patch_results:
            #     with open(crash_folder / "patch.json", "w") as f:
            #         f.write(self._patch_results[crash_type].model_dump_json())

            if crash_type in self._crash_analysis:
                with open(crash_folder / "analysis.json", "w") as f:
                    f.write(self._crash_analysis[crash_type].model_dump_json())

            if crash_type in self._minimized_crashes:
                with open(crash_folder / "minimized.json", "w") as f:
                    f.write(self._minimized_crashes[crash_type].model_dump_json())

                with open(crash_folder / "minimized.cpp", "w") as f:
                    f.write(self._minimized_crashes[crash_type].minimized_code)

                with open(crash_folder / "minimized.report", "w") as f:
                    f.write(self._minimized_crashes[crash_type].minimized_crash_report)

                with open(crash_folder / "original.cpp", "w") as f:
                    f.write(self._minimized_crashes[crash_type].external_code)

                with open(crash_folder / "original.report", "w") as f:
                    f.write(self._minimized_crashes[crash_type].crash_report)

            for crash in crashes:
                name = crash.name.replace('.', '_')
                # with open(crash_folder / f"{name}.graph", "w") as f:
                #     f.write(crash.minimized_graph)

                # with open(crash_folder / f"{name}.cpp", "w") as f:
                #     f.write(crash.external_code)

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
        # print(f'{Colors.YELLOW}QUEUE{Colors.END}')
        # self.update_queue(cfg)
        print(f'{Colors.YELLOW}SAVE{Colors.END}')
        self.save_results()
        return found_new

    def save_results(self):
        self.save_crash_buckets()
        # self.save_safe_corpus()

    def print_stats(self):
        print('-' * 40)
        by_type = self.crashes_by_type()
        counts = [(k, len(v)) for k, v in by_type.items()]
        counts.sort(key=lambda x: x[1], reverse=True)
        for k, v in counts:
            hsh = hashlib.sha256(k.encode()).hexdigest()[:8]
            print(f'{Colors.PURPLE}{hsh}{Colors.END} :: ({Colors.GREEN}{v}{Colors.END}) {Colors.YELLOW}{k}{Colors.END}')
            # if k in self._triage_results:
            #     print(f'  {Colors.GREEN}Triaged{Colors.END} {self._triage_results[k].explanation_of_bug}')
            # else:
            #     print(f'  {Colors.RED}Not triaged{Colors.END}')

            # if k in self._patch_results:
            #     print(f'  {Colors.GREEN}Patched{Colors.END}')
            #     self._patch_results[k].pprint()
            # else:
            #     print(f'  {Colors.RED}Not patched{Colors.END}')

            if k in self._crash_analysis:
                print(f'  {Colors.GREEN}Crash analysis{Colors.END}')
                self._crash_analysis[k].pprint()
            else:
                print(f'  {Colors.RED}Not analyzed{Colors.END}')

            if k in self._minimized_crashes:
                print(f'  {Colors.GREEN}Minimized{Colors.END}')
            else:
                print(f'  {Colors.RED}Not minimized{Colors.END}')
        print('-' * 40)

    def attempt_minimize(self, cfg: Config):
        """Attempt to minimize crashes"""
        for crash in self._crashes:
            # Skip if we already have a minimized crash for this crash type
            if crash.crash_summary in self._minimized_crashes:
                continue

            print(f'{Colors.YELLOW}Minimize {crash.name} ({crash.crash_summary}){Colors.END}')
            
            # Try to reproduce the crash
            with tempfile.TemporaryDirectory() as tmpdir:
                res = try_generate_reproducer(Path(tmpdir), str(self.fuzzer_path), cfg.info.build_script_ext, crash.filepath)
                if res is None:
                    print(f'{Colors.RED}Failed to generate reproducer for {crash.name}{Colors.END}')
                    continue

                # Run minimizer agent
                code, report = run_minimizer_agent(
                    cfg.info,
                    cfg.model,
                    str(res.external_code),
                )

                res.minimized_code = code
                res.minimized_crash_report = report

                self._minimized_crashes[crash.crash_summary] = res
                self.save_results()

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
                    cfg.info,
                    cfg.model,
                    str(crash_cpp),
                    str(crash_report),
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

            print(f'{Colors.YELLOW}Patch {k} ({v} crashes){Colors.END}')

            with tempfile.TemporaryDirectory() as tmpdir:
                analysis_file = Path(tmpdir) / "analysis.json"
                analysis_file.write_text(triage_analysis.model_dump_json())

                examples_invalid = Path(tmpdir) / "examples_invalid"
                examples_invalid.mkdir(parents=True, exist_ok=True)

                crash_cpp = Path(tmpdir) / "crash.cpp"
                crash_cpp.write_text(by_type[k][0].external_code)

                for crash in by_type[k]:
                    with open(examples_invalid / crash.name, "w") as f:
                        f.write(crash.minimized_graph)

                testcase = Path(tmpdir) / by_type[k][0].name

                res = run_patch_agent(
                    cfg.info,
                    cfg.model,
                    str(self.schema_path),
                    analysis=str(analysis_file),
                    testcase=str(testcase),
                    fuzzer=str(self.fuzzer_path),
                    ex_valid=str(self.safe_corpus_path),
                    ex_invalid=str(examples_invalid),
                    minimized_crash=str(crash_cpp),
                )

                res.pprint()
                if res.fix is None:
                    print(f'{Colors.RED}No patch found for {k}{Colors.END}')
                    continue

                self._patch_results[k] = res
                self.save_results()

    def attempt_crash_analysis(self, cfg: Config, runner: 'Runner'):
        by_type = self.crashes_by_type()
        counts = [(k, len(v)) for k, v in by_type.items()]
        counts.sort(key=lambda x: x[1], reverse=True)

        for k, v in counts:
            if k in self._crash_analysis:
                continue

            if v < 5:
                # Need at least 5 crashes to run crash analysis
                continue

            history = []
            for stage in runner.stages[:-1]:
                if k in stage._crash_analysis:
                    history.append(stage._crash_analysis[k])

            print(f'{Colors.YELLOW}Crash analysis {k} ({v} crashes) ({len(history)} history){Colors.END}')

            with tempfile.TemporaryDirectory() as tmpdir:
                crash_report = Path(tmpdir) / "crash.report"
                crash_report.write_text(by_type[k][0].crash_report)

                crash_cpp = Path(tmpdir) / "crash.cpp"
                crash_cpp.write_text(by_type[k][0].external_code)
                
                res = run_crash_analysis_agent(
                    cfg.info,
                    cfg.model,
                    str(crash_cpp),
                    str(crash_report),
                    str(self.schema_path),
                    history,
                )

                self._crash_analysis[k] = res
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
            stage.ensure_running(self.cfg.bypass_validation)

            while True:
                stage.update_tick(self.cfg)
                
                # try:
                    # stage.attempt_triage(self.cfg)
                    # stage.attempt_patch(self.cfg)
                    # stage.attempt_crash_analysis(self.cfg, self)
                stage.attempt_minimize(self.cfg)
                # except Exception as e:
                #     # Probably LLM error, wait and try again
                #     print(f'{Colors.RED}Error: {e}{Colors.END}')
                #     time.sleep(10)

                stage.save_results()
                stage.print_stats()

                has_patches = False
                if len(stage._crash_analysis) > 0:
                    for result in stage._crash_analysis.values():
                        if result.fix is not None and isinstance(result.fix.fix, EndpointFixAttempt):
                            # Bail out and apply patches
                            has_patches = True
                            break

                if has_patches:
                    break

                time.sleep(1)

            stage.stop_fuzzer()

            print(f'{Colors.YELLOW}Applying patches...{Colors.END}')
            for result in stage._crash_analysis.values():
                if result.fix is not None and isinstance(result.fix.fix, EndpointFixAttempt):
                    for fix in result.fix.fix.fixes:
                        print(f'{Colors.YELLOW}Patching endpoint {fix.endpoint_name}...{Colors.END}')
                        for endpoint in current_schema.endpoints:
                            if endpoint.name == fix.endpoint_name:
                                endpoint.template = fix.new_template
                                break

            # for k, v in stage._patch_results.items():
            #     print(f'{Colors.YELLOW}Applying patch for {k}...{Colors.END}')
            #     match v.fix:
            #         case EndpointFixAttempt():
            #             print(f'{Colors.YELLOW}Patching endpoints {k}...{Colors.END}')
            #             for fix in v.fix.fixes:
            #                 print(f'{Colors.YELLOW}Patching endpoint {fix.endpoint_name}...{Colors.END}')
            #                 for endpoint in current_schema.endpoints:
            #                     if endpoint.name == fix.endpoint_name:
            #                         endpoint.template = fix.new_template
            #                         break
            #         case FilterResult():
            #             print(f'{Colors.YELLOW}Patching filter {k}...{Colors.END}')
            #             current_filters.append(v.fix.filter_attempt.filter_function)
            #         case _:
            #             raise ValueError(f'Unknown patch type: {type(v.fix)}')

def main(args):
    info = ProjectInfo.model_validate_json(open(args.info).read())
    runner = Runner(Config(
        info=info,
        model=args.model,
        schema=args.schema,
        workdir=args.workdir,
        bypass_validation=args.bypass_validation
    ))
    runner.run()


def register(subparsers):
    parser = subparsers.add_parser('run', help='Run the fuzzer')
    parser.add_argument('--info', type=str, required=True, help='Path to the project info file')
    parser.add_argument('--model', type=str, required=True, help='Model to use')
    parser.add_argument('--schema', type=str, required=True, help='Path to the initial fuzzer schema')
    parser.add_argument('--workdir', type=str, required=True, help='Path to the working directory')
    parser.add_argument('--bypass-validation', action='store_true', help='Bypass validation of fuzzer schema')
    parser.set_defaults(func=main)
