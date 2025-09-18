from argparse import ArgumentParser
from pathlib import Path
import shutil
import subprocess
import time
import os
import signal
from pydantic import BaseModel
import tempfile

from ..data.project_info import ProjectInfo
from .data import ProjectMetadata
from ..utils.colors import Colors   
from .distill_agent import CrashAssessment
from .patcher import PatchInfo


class CrashInfo(BaseModel):
    bucket_hash: str
    summary: str
    original_testcase: str
    harness_revision: int


class RestartableProcess:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.p = None

    def launch(self):
        self.p = subprocess.Popen(*self.args, **self.kwargs)

    def is_running(self) -> bool:
        return self.p is not None and self.p.poll() is None

    def terminate(self, sig: int = signal.SIGTERM):
        os.killpg(self.p.pid, sig)
        try:
            self.p.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            os.killpg(self.p.pid, sig)
            self.p.wait()


def get_last_fuzzer_line(log_path: Path, last_chunk_size: int = 3000) -> str:
    '''Read last 3000 bytes'''
    with open(log_path, 'rb') as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - last_chunk_size), os.SEEK_SET)
        chunk = f.read(last_chunk_size)
        chunk = chunk.decode('latin1').split('\n')

        for line in chunk[::-1]:
            if 'UserStats' in line:
                return line

    return None


def parse_fuzzer_stats(line: str) -> dict:
    walltime = line.split(' ')[0]
    obj = line.split('objectives: ')[1].split(',')[0]
    cov = line.split('edges: ')[1].split(' ')[0]
    cov_a, cov_b = cov.split('/')

    walltime = float(walltime[:-1])
    obj = int(obj)
    cov_pct = int(cov_a) / int(cov_b)

    return {
        'time': walltime,
        'obj': obj,
        'cov': cov_pct
    }


def compile_harness(workdir: Path):
    print(f'{Colors.GREEN}[+]{Colors.END} Converting harness: {workdir}')
    subprocess.run(['agfi', 'zephyr', 'to-schema', '--meta', workdir / 'harness.json', '--output', workdir / 'fuzzer.json'])

    print(f'{Colors.GREEN}[+]{Colors.END} Building fuzzer: {workdir}')
    subprocess.run(['agfi', 'build', 'full', workdir / 'fuzzer'])


def run_campaign(campaign: Path, info: str):
    # Collect the list of known crashes
    reports = campaign / 'reports'
    known_crashes: dict[str, CrashInfo] = {}
    for report_dir in reports.iterdir():
        if report_dir.is_dir():
            crash_info = CrashInfo.model_validate_json(open(report_dir / 'info.json').read())
            known_crashes[crash_info.bucket_hash] = crash_info

    print(f'{Colors.GREEN}[+]{Colors.END} Known crashes: {len(known_crashes)}')

    # Find the highest revision in campaign/revisions
    current_revision = 0
    for revision in (campaign / 'revisions').iterdir():
        if revision.is_dir():
            current_revision = max(current_revision, int(revision.name))
    
    print(f'{Colors.GREEN}[+]{Colors.END} Current revision: {current_revision}')

    workdir = (campaign / 'revisions' / str(current_revision)).absolute()
    compile_harness(workdir)

    # Run harness once to create the pspec cache
    print(f'{Colors.GREEN}[+]{Colors.END} Running harness once to create the pspec cache: {workdir}')
    subprocess.run([
        str(workdir / 'fuzzer'),
        '--bypass-validation',
    ], cwd=workdir)

    (workdir / 'outputs').mkdir(parents=True, exist_ok=True)
    (workdir / 'outputs' / 'crashes').mkdir(parents=True, exist_ok=True)
    (workdir / 'outputs' / 'queue').mkdir(parents=True, exist_ok=True)
    (workdir / 'buckets').mkdir(parents=True, exist_ok=True)

    fuzzer_bin = workdir / 'fuzzer'
    fuzzer_inputs = workdir / 'outputs' / 'queue'
    fuzzer_outputs = workdir / 'outputs'
    fuzzer_crashes = workdir / 'outputs' / 'crashes'
    fuzzer_log = workdir / 'fuzzer.log'
    
    bucketizer_buckets = workdir / 'buckets'

    p_fuzzer = RestartableProcess([
        str(fuzzer_bin),
        '-i', str(fuzzer_inputs),
        '-o', str(fuzzer_outputs),
        '--bypass-validation',
        '--logfile', str(fuzzer_log),
        '--rss-limit-mb', '0',
    ], cwd=workdir, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    p_bucketizer = RestartableProcess([
        'agfi', 'bucketizer',
        '--info', info,
        '--fuzzer', str(fuzzer_bin),
        '--crashes', str(fuzzer_crashes),
        '--output', str(bucketizer_buckets),
        '--nproc', '1',
        '--watch',
    ], cwd=workdir, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    crashes_seen_this_round = set()
    new_crash = None
    new_crash_hash = None
    new_crash_summary = None

    status_refresh = 15.0
    last_status_check = time.time()

    line_buffer = []

    try:
        print(f'{Colors.GREEN}[+]{Colors.END} Starting fuzzer and bucketizer')
        p_fuzzer.launch()
        p_bucketizer.launch()

        while new_crash is None:
            current_time = time.time()
            if current_time - last_status_check >= status_refresh:
                # Read the last part of fuzzer.log
                line = get_last_fuzzer_line(fuzzer_log)
                if line is not None:
                    line_buffer.append(line)
                    line_buffer = line_buffer[-4:]
                    print(f'{Colors.YELLOW}[*]{Colors.END} {line}')
                else:
                    print(f'{Colors.YELLOW}[*]{Colors.END} No fuzzer stats found')
                last_status_check = current_time

            if not p_fuzzer.is_running():
                print(f'{Colors.RED}[-]{Colors.END} Fuzzer crashed, restarting')
                p_fuzzer.launch()

            if len(line_buffer) == 4 and line_buffer[0] == line_buffer[-1]:
                print(f'{Colors.YELLOW}[*]{Colors.END} Fuzzer is stuck, restarting')
                p_fuzzer.terminate(signal.SIGKILL)
                p_fuzzer.launch()

            if not p_bucketizer.is_running():
                print(f'{Colors.RED}[-]{Colors.END} Bucketizer crashed, restarting')
                p_bucketizer.launch()

            # Check for new crashes
            for bucket_dir in bucketizer_buckets.iterdir():
                if bucket_dir.is_dir() and not bucket_dir.name.startswith('.'):
                    bucket_hash = bucket_dir.name
                    summary = (bucket_dir / 'desc.txt').read_text().strip()
                    if bucket_hash not in known_crashes:
                        # Check if a repro exists
                        repro = list((bucket_dir / 'repro').glob('*.cpp'))
                        if len(repro) == 0:
                            continue
                        repro = repro[0]
                        
                        print(f'{Colors.GREEN}[+]{Colors.END} New crash: ({bucket_hash}) {summary} -- {repro}')
                        new_crash = repro
                        new_crash_hash = bucket_hash
                        new_crash_summary = summary
                        break
                    else:
                        if bucket_hash not in crashes_seen_this_round:
                            print(f'{Colors.YELLOW}[*]{Colors.END} Hit known crash: ({bucket_hash}) {summary}')
                            crashes_seen_this_round.add(bucket_hash)
            
            time.sleep(5)
    except KeyboardInterrupt:
        print(f'{Colors.RED}[-]{Colors.END} Quitting...')
        p_fuzzer.terminate()
        p_bucketizer.terminate()
        exit(0)
    except Exception as e:
        print(f'{Colors.RED}[-]{Colors.END} Error: {e}')
        p_fuzzer.terminate()
        p_bucketizer.terminate()
        exit(1)

    # Kill the fuzzer and bucketizer
    print(f'{Colors.GREEN}[+]{Colors.END} Killing fuzzer and bucketizer')
    p_fuzzer.terminate()
    p_bucketizer.terminate()
    print(f'{Colors.GREEN}[+]{Colors.END} Fuzzer and bucketizer killed')

    # Save the report into the campaign/reports directory
    report_path = campaign / 'reports' / new_crash_hash
    report_path.mkdir(parents=True, exist_ok=True)
    (report_path / 'info.json').write_text(CrashInfo(
        bucket_hash=new_crash_hash,
        summary=new_crash_summary,
        original_testcase=str(new_crash),
        harness_revision=current_revision
    ).model_dump_json(indent=2))

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        assessment_path = tmpdir / 'assessment.json'
        try:
            subprocess.run([
                'agfi', 'zephyr', 'distill-agent',
                '--harness', str((workdir / 'harness.json').absolute()),
                '--external-code', str(new_crash),
                '--output', str(assessment_path)
            ], check=True)
        except subprocess.CalledProcessError:
            print(f'{Colors.RED}[-]{Colors.END} Distill agent failed')
            return
        
        assessment = CrashAssessment.model_validate_json(open(assessment_path).read())
        print(f'{Colors.GREEN}[+]{Colors.END} Crash assessment: [patchable: {assessment.can_patch_fuzzer}] ({assessment.crash_type}) {assessment.title}')
        
        # Save the assessment into the campaign/reports directory
        (report_path / 'assessment.json').write_text(assessment.model_dump_json(indent=2))

        if not assessment.can_patch_fuzzer:
            print(f'{Colors.RED}[-]{Colors.END} Crash is not patchable')
            return

        # Otherwise, run the patcher and make a new revision if it succeeds
        print(f'{Colors.GREEN}[+]{Colors.END} Running patcher')
        patch_path = tmpdir / 'patch.json'
        try:
            subprocess.run([
                'agfi', 'zephyr', 'patcher',
                '--info', info,
                '--harness', str((workdir / 'harness.json').absolute()),
                '--assessment', str(assessment_path),
                '--output', str(patch_path)
            ])
        except subprocess.CalledProcessError:
            print(f'{Colors.RED}[-]{Colors.END} Patcher failed')
            return

        # Save the patch into the campaign/reports directory
        patch_info = PatchInfo.model_validate_json(open(patch_path).read())
        (report_path / 'patch.json').write_text(patch_info.model_dump_json(indent=2))

        # Make a new revision
        print(f'{Colors.GREEN}[+]{Colors.END} Making new revision')
        new_harness = ProjectMetadata.model_validate_json(open((workdir / 'harness.json').absolute()).read())

        stubs = {x.name: x for x in new_harness.endpoints}
        for stub in patch_info.modified_stubs:
            stubs[stub.name] = stub
        new_harness.endpoints = list(stubs.values())

        next_revision = current_revision + 1
        (campaign / 'revisions' / str(next_revision)).mkdir(parents=True, exist_ok=True)
        (campaign / 'revisions' / str(next_revision) / 'harness.json').write_text(new_harness.model_dump_json(indent=2))

        print(f'{Colors.GREEN}[+]{Colors.END} New revision created: {next_revision}')
        return


def main(args):
    info_path = Path(args.info).absolute()
    harness = ProjectMetadata.model_validate_json(open(args.harness).read())
    campaign = Path(args.campaign).absolute()

    if campaign.exists() and args.reset:
        print(f'{Colors.GREEN}[+]{Colors.END} Clearing old campaign: {campaign}')
        shutil.rmtree(campaign)

    if not campaign.exists():
        print(f'{Colors.GREEN}[+]{Colors.END} Creating new campaign: {campaign}')
        campaign.mkdir(parents=True, exist_ok=True)
        (campaign / 'reports').mkdir(parents=True, exist_ok=True)
        (campaign / 'revisions' / '0').mkdir(parents=True, exist_ok=True)
        # Copy the original harness to the campaign
        (campaign / 'revisions' / '0' / 'harness.json').write_text(harness.model_dump_json(indent=2))
    else:
        print(f'{Colors.GREEN}[+]{Colors.END} Using existing campaign: {campaign}')

    # Run the campaign
    run_campaign(campaign, info_path)


def register(subparser: ArgumentParser):
    parser = subparser.add_parser('looper')
    parser.add_argument('--info', type=str, default='info.json', help='Path to the project info')
    parser.add_argument('--harness', type=str, required=True, help='Path to the original harness')
    parser.add_argument('--campaign', type=str, required=True, help='Path to the campaign directory')
    parser.add_argument('--reset', action='store_true', help='Reset the campaign (if it exists)')
    parser.set_defaults(func=main)
