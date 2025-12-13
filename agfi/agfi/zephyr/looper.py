from argparse import ArgumentParser
from pathlib import Path
import shutil
import subprocess
import time
import os
import signal
from datetime import datetime
from typing import Optional
from pydantic import BaseModel
import tempfile
import json

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


def _default_stop_file(campaign: Path, persist: Optional[Path]) -> Path:
    """
    Compute the default "stop requested" file location.

    In minimal-persistence mode, `persist` is mounted from the host, so placing
    the control file under persist makes it easy for external managers to
    request a stop without signalling/killing the process.
    """
    root = persist if persist is not None else campaign
    return (root / 'control' / 'stop').absolute()


def _stop_requested(stop_file: Optional[Path]) -> bool:
    try:
        return stop_file is not None and stop_file.exists()
    except Exception:
        return False


def _ack_stop(stop_file: Optional[Path]):
    """
    Best-effort acknowledgement of a stop request.

    We write a small `stop.ack` file (timestamp) and remove the request file so
    subsequent runs don't immediately exit unless a new request is created.
    """
    if stop_file is None:
        return
    try:
        if not stop_file.exists():
            return
        stop_file.parent.mkdir(parents=True, exist_ok=True)
        ack_path = stop_file.parent / 'stop.ack'
        ack_path.write_text(
            json.dumps(
                {
                    'handled_at': datetime.utcnow().isoformat() + 'Z',
                },
                indent=2,
            )
        )
        stop_file.unlink(missing_ok=True)
    except Exception:
        # Never fail the looper due to stop-file bookkeeping.
        return


def get_last_fuzzer_line(log_path: Path, last_chunk_size: int = 3000) -> str:
    '''Read last 3000 bytes'''
    if not log_path.exists():
        return None
    with open(log_path, 'rb') as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - last_chunk_size), os.SEEK_SET)
        chunk = f.read(last_chunk_size)
        chunk = chunk.decode('latin1').split('\n')

        if len(chunk) > 2:
            return chunk[-2]
        else:
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


def index_corpus_file(corpus_path: Path) -> set:
    """Return the set of endpoint names used in a corpus file."""
    import json
    try:
        with open(corpus_path, 'r') as f:
            data = json.load(f)
            endpoints = set()
            if 'graph' in data and 'nodes' in data['graph']:
                for node in data['graph']['nodes']:
                    endpoints.add(node['typ'])
            return endpoints
    except Exception:
        return set()


def copy_compatible_corpus(prev_revision_dir: Path, next_revision_dir: Path, 
                           modified_endpoints: set) -> int:
    """Copy corpus files from previous revision that don't use modified endpoints.
    Returns the number of files copied."""
    prev_queue = prev_revision_dir / 'outputs' / 'queue'
    if not prev_queue.exists():
        return 0
    
    next_inputs = next_revision_dir / 'inputs'
    next_inputs.mkdir(parents=True, exist_ok=True)
    
    copied = 0
    for corpus_file in prev_queue.iterdir():
        if not corpus_file.is_file() or corpus_file.name.startswith('.'):
            continue
        
        try:
            used_endpoints = index_corpus_file(corpus_file)
            # Only copy if none of the modified endpoints are used
            if not used_endpoints.intersection(modified_endpoints):
                shutil.copy(corpus_file, next_inputs / corpus_file.name)
                copied += 1
        except Exception as e:
            # If we can't parse the file, skip it
            print(f'{Colors.YELLOW}[!]{Colors.END} Could not index {corpus_file.name}: {e}')
            continue
    
    return copied


def compile_harness(workdir: Path):
    print(f'{Colors.GREEN}[+]{Colors.END} Converting harness: {workdir}')
    subprocess.run(['agfi', 'zephyr', 'to-schema', '--meta', workdir / 'harness.json', '--output', workdir / 'fuzzer.json'])

    print(f'{Colors.GREEN}[+]{Colors.END} Building fuzzer: {workdir}')
    subprocess.run(['agfi', 'build', 'full', workdir / 'fuzzer'])


def _dir_size_bytes(path: Path) -> int:
    """
    Best-effort recursive directory size. Used to decide when to rotate the
    ephemeral workdir in minimal-persistence mode.
    """
    total = 0
    try:
        for root, _, files in os.walk(path, followlinks=False):
            for f in files:
                try:
                    fp = os.path.join(root, f)
                    total += os.path.getsize(fp)
                except OSError:
                    continue
    except OSError:
        return 0
    return total


def _copy_newest_files(src_dir: Path, dst_dir: Path, keep: int) -> int:
    """
    Copy up to `keep` newest regular files from src_dir into dst_dir (flat),
    overwriting existing.
    """
    if keep <= 0 or not src_dir.exists():
        return 0
    dst_dir.mkdir(parents=True, exist_ok=True)
    files = [p for p in src_dir.iterdir() if p.is_file() and not p.name.startswith('.')]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    copied = 0
    for p in files[:keep]:
        try:
            shutil.copy2(p, dst_dir / p.name)
            copied += 1
        except Exception:
            continue
    return copied


def _restore_seed_dir(seed_dir: Path, inputs_dir: Path):
    if not seed_dir.exists():
        return
    inputs_dir.mkdir(parents=True, exist_ok=True)
    for p in seed_dir.iterdir():
        if p.is_file() and not p.name.startswith('.'):
            try:
                shutil.copy2(p, inputs_dir / p.name)
            except Exception:
                continue


def _build_scratch_env(scratch_dir: Path) -> dict:
    env = os.environ.copy()
    env['TMPDIR'] = str(scratch_dir)
    env['HOME'] = str(scratch_dir)
    env['XDG_CACHE_HOME'] = str(scratch_dir / '.cache')
    return env


def run_campaign(
    campaign: Path,
    info: str,
    revise: bool,
    no_corpus_copy: bool = False,
    *,
    persist: Optional[Path] = None,
    rotate_seconds: Optional[int] = None,
    rotate_max_gb: Optional[float] = None,
    rotate_seed_keep: int = 0,
    stop_file: Optional[Path] = None,
):
    # Collect the list of known crashes
    reports = (persist / 'reports') if persist is not None else (campaign / 'reports')
    reports.mkdir(parents=True, exist_ok=True)
    known_crashes: dict[str, CrashInfo] = {}
    prior_crash_history: list[dict] = []
    for report_dir in reports.iterdir():
        if report_dir.is_dir():
            info_path = report_dir / 'info.json'
            if not info_path.exists():
                continue
            crash_info = CrashInfo.model_validate_json(open(info_path).read())
            known_crashes[crash_info.bucket_hash] = crash_info

            # If an assessment exists, include a compact summary in the history so
            # the distill-agent can recognize duplicates of previously-seen bugs.
            assessment_path = report_dir / 'assessment.json'
            if assessment_path.exists():
                try:
                    assessment = CrashAssessment.model_validate_json(open(assessment_path).read())
                    prior_crash_history.append(
                        {
                            "bug_hash": crash_info.bucket_hash,
                            "summary": crash_info.summary,
                            "crash_type": assessment.crash_type,
                            "title": assessment.title,
                            "harness_revision": crash_info.harness_revision,
                            "relevant_functions": assessment.relevant_functions,
                            "duplicate_of": getattr(assessment, "duplicate_of", None),
                        }
                    )
                except Exception:
                    # If the assessment cannot be parsed for any reason, just skip it.
                    continue

    print(f'{Colors.GREEN}[+]{Colors.END} Known crashes: {len(known_crashes)}')

    # Stop-request control file. External managers can request a safe stop by
    # creating this file; we only honor it at safe points (between fuzzing and
    # triage, or between revisions).
    if stop_file is None:
        stop_file = _default_stop_file(campaign, persist)
    try:
        stop_file.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    # Find the highest revision in campaign/revisions
    current_revision = 0
    if persist is not None:
        # In minimal-persistence mode, revisions are persisted as small harness.json
        # files so patch evolution can resume across container restarts.
        persisted_revs = persist / 'revisions'
        if persisted_revs.exists():
            for revision in persisted_revs.iterdir():
                if revision.is_dir() and revision.name.isdigit():
                    current_revision = max(current_revision, int(revision.name))
    else:
        for revision in (campaign / 'revisions').iterdir():
            if revision.is_dir() and revision.name.isdigit():
                current_revision = max(current_revision, int(revision.name))
    
    print(f'{Colors.GREEN}[+]{Colors.END} Current revision: {current_revision}')

    workdir = (campaign / 'revisions' / str(current_revision)).absolute()
    # Ensure the ephemeral workdir has the right harness.json (for restarts).
    if persist is not None:
        src_harness = (persist / 'revisions' / str(current_revision) / 'harness.json').absolute()
        if src_harness.exists():
            workdir.mkdir(parents=True, exist_ok=True)
            shutil.copy(src_harness, workdir / 'harness.json')
    compile_harness(workdir)

    # Run harness once to create the pspec cache
    print(f'{Colors.GREEN}[+]{Colors.END} Running harness once to create the pspec cache: {workdir}')
    subprocess.run([
        str(workdir / 'fuzzer'),
        '--bypass-validation',
    ], cwd=workdir)

    # Prepare seed inputs. If outputs/queue exists, copy it to inputs
    (workdir / 'inputs').mkdir(parents=True, exist_ok=True)
    if (workdir / 'outputs' / 'queue').exists():
        print(f'{Colors.GREEN}[+]{Colors.END} Copying seed inputs from outputs/queue to inputs')
        shutil.copytree(workdir / 'outputs' / 'queue', workdir / 'inputs', dirs_exist_ok=True)

    (workdir / 'outputs').mkdir(parents=True, exist_ok=True)
    (workdir / 'outputs' / 'crashes').mkdir(parents=True, exist_ok=True)
    (workdir / 'outputs' / 'queue').mkdir(parents=True, exist_ok=True)

    fuzzer_bin = workdir / 'fuzzer'
    fuzzer_inputs = workdir / 'inputs'
    fuzzer_outputs = workdir / 'outputs'
    fuzzer_crashes = workdir / 'outputs' / 'crashes'
    fuzzer_log = workdir / 'fuzzer.log'
    
    # Bucketizer output is persisted in minimal-persistence mode.
    if persist is not None:
        bucketizer_buckets = (persist / 'buckets' / str(current_revision)).absolute()
    else:
        bucketizer_buckets = (workdir / 'buckets').absolute()
    bucketizer_buckets.mkdir(parents=True, exist_ok=True)

    # Run the target with a scratch cwd so relative-path writes don't land in the
    # campaign directory. In minimal-persistence mode this also makes it easy to
    # wipe bulk writes during rotation.
    scratch_dir = (campaign / 'scratch' / str(current_revision) / 'current').absolute()
    scratch_dir.mkdir(parents=True, exist_ok=True)
    scratch_env = _build_scratch_env(scratch_dir)

    p_fuzzer = RestartableProcess([
        str(fuzzer_bin),
        '-i', str(fuzzer_inputs),
        '-o', str(fuzzer_outputs),
        '--bypass-validation',
        '--logfile', str(fuzzer_log)
    ], cwd=scratch_dir, env=scratch_env, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Keep output for bucketizer to help debug problems with crash reproduction
    p_bucketizer = RestartableProcess([
        'agfi', 'bucketizer',
        '--info', info,
        '--fuzzer', str(fuzzer_bin),
        '--crashes', str(fuzzer_crashes),
        '--output', str(bucketizer_buckets),
        '--nproc', '1',
        '--watch',
    ], cwd=scratch_dir, env=scratch_env, start_new_session=True)

    crashes_seen_this_round = set()
    new_crash = None
    new_crash_hash = None
    new_crash_summary = None
    stop_requested = False

    status_refresh = 15.0
    last_status_check = time.time()

    line_buffer = []

    # Rotation bookkeeping (minimal-persistence mode only).
    last_rotate = time.time()
    rotate_check_refresh = 60.0
    last_rotate_check = time.time()
    rotate_max_bytes = None
    if rotate_max_gb is not None:
        try:
            rotate_max_bytes = int(float(rotate_max_gb) * (1024 ** 3))
        except Exception:
            rotate_max_bytes = None

    try:
        print(f'{Colors.GREEN}[+]{Colors.END} Starting fuzzer and bucketizer')
        p_fuzzer.launch()
        p_bucketizer.launch()

        # If revise is True, stop when we find a new crash
        # If revise is False, continue indefinitely
        while new_crash is None or (not revise):
            # Safe stop point: while fuzzing/bucketizing, before we begin triage.
            if _stop_requested(stop_file):
                print(f'{Colors.YELLOW}[*]{Colors.END} Stop requested; finishing current work and exiting safely')
                stop_requested = True
                break

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

            # Periodically rotate the ephemeral workdir to bound disk usage.
            if persist is not None and (rotate_seconds is not None or rotate_max_bytes is not None):
                if current_time - last_rotate_check >= rotate_check_refresh:
                    should_rotate = False
                    reasons = []
                    if rotate_seconds is not None and current_time - last_rotate >= rotate_seconds:
                        should_rotate = True
                        reasons.append(f'time>{rotate_seconds}s')
                    if rotate_max_bytes is not None:
                        size_bytes = _dir_size_bytes(campaign)
                        if size_bytes >= rotate_max_bytes:
                            should_rotate = True
                            gb = size_bytes / (1024 ** 3)
                            reasons.append(f'size~{gb:.2f}GB>{rotate_max_gb}GB')

                    if should_rotate:
                        print(
                            f'{Colors.YELLOW}[*]{Colors.END} Rotating ephemeral workdir '
                            f'({", ".join(reasons)})'
                        )
                        # Stop processes
                        try:
                            p_fuzzer.terminate()
                        except Exception:
                            pass
                        try:
                            p_bucketizer.terminate()
                        except Exception:
                            pass

                        # Snapshot a small seed set from the queue to persisted seeds.
                        seed_src = workdir / 'outputs' / 'queue'
                        seed_dst = (persist / 'seeds' / 'current').absolute()
                        if seed_dst.exists():
                            shutil.rmtree(seed_dst, ignore_errors=True)
                        copied = _copy_newest_files(seed_src, seed_dst, rotate_seed_keep)
                        print(f'{Colors.YELLOW}[*]{Colors.END} Saved {copied} seed inputs')

                        # Wipe bulk ephemeral directories.
                        shutil.rmtree(scratch_dir, ignore_errors=True)
                        shutil.rmtree(workdir / 'outputs', ignore_errors=True)
                        shutil.rmtree(workdir / 'inputs', ignore_errors=True)

                        # Recreate scratch + inputs/outputs and restore seeds.
                        scratch_dir.mkdir(parents=True, exist_ok=True)
                        scratch_env = _build_scratch_env(scratch_dir)
                        (workdir / 'inputs').mkdir(parents=True, exist_ok=True)
                        _restore_seed_dir(seed_dst, workdir / 'inputs')
                        (workdir / 'outputs' / 'crashes').mkdir(parents=True, exist_ok=True)
                        (workdir / 'outputs' / 'queue').mkdir(parents=True, exist_ok=True)

                        # Relaunch processes
                        p_fuzzer.kwargs['cwd'] = scratch_dir
                        p_bucketizer.kwargs['cwd'] = scratch_dir
                        p_fuzzer.kwargs['env'] = scratch_env
                        p_bucketizer.kwargs['env'] = scratch_env
                        p_fuzzer.launch()
                        p_bucketizer.launch()
                        last_rotate = time.time()

                    last_rotate_check = current_time

            if not p_fuzzer.is_running():
                print(f'{Colors.RED}[-]{Colors.END} Fuzzer crashed, restarting')
                p_fuzzer.launch()

            # if len(line_buffer) == 4 and line_buffer[0] == line_buffer[-1]:
            #     print(f'{Colors.YELLOW}[*]{Colors.END} Fuzzer is stuck, restarting')
            #     p_fuzzer.terminate(signal.SIGKILL)
            #     p_fuzzer.launch()

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

    # If a stop was requested during fuzzing, exit now (safe point).
    if stop_requested:
        _ack_stop(stop_file)
        return True

    # If a stop was requested right after we discovered a new crash (or between
    # the end of fuzzing and the start of triage), persist a minimal report and
    # exit without doing long-running triage/patching work.
    if _stop_requested(stop_file):
        if new_crash_hash is not None:
            report_path = reports / new_crash_hash
            report_path.mkdir(parents=True, exist_ok=True)
            (report_path / 'info.json').write_text(
                CrashInfo(
                    bucket_hash=new_crash_hash,
                    summary=new_crash_summary or '',
                    original_testcase=str(new_crash) if new_crash is not None else '',
                    harness_revision=current_revision,
                ).model_dump_json(indent=2)
            )
        print(f'{Colors.YELLOW}[*]{Colors.END} Stop requested; skipping triage/patching and exiting safely')
        _ack_stop(stop_file)
        return True

    # Save the report into the campaign/reports directory
    report_path = reports / new_crash_hash
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
        history_path = tmpdir / 'history.json'
        # Serialize prior crash history for the distill-agent, but keep it compact.
        try:
            history_path.write_text(json.dumps(prior_crash_history, indent=2))
            history_arg = ['--history', str(history_path)]
        except Exception:
            history_arg = []
        try:
            subprocess.run([
                'agfi', 'zephyr', 'distill-agent',
                '--harness', str((workdir / 'harness.json').absolute()),
                '--external-code', str(new_crash),
                '--output', str(assessment_path),
            ] + history_arg, check=True)
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
                'agfi', 'zephyr', 'patcher-agent',
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
        next_revision_dir = campaign / 'revisions' / str(next_revision)
        next_revision_dir.mkdir(parents=True, exist_ok=True)
        (next_revision_dir / 'harness.json').write_text(new_harness.model_dump_json(indent=2))
        if persist is not None:
            persist_rev_dir = (persist / 'revisions' / str(next_revision)).absolute()
            persist_rev_dir.mkdir(parents=True, exist_ok=True)
            (persist_rev_dir / 'harness.json').write_text(new_harness.model_dump_json(indent=2))

        # Copy compatible corpus files from previous revision
        if not no_corpus_copy:
            modified_endpoint_names = {stub.name for stub in patch_info.modified_stubs}
            print(f'{Colors.GREEN}[+]{Colors.END} Copying compatible corpus files (excluding endpoints: {", ".join(modified_endpoint_names)})')
            copied_count = copy_compatible_corpus(workdir, next_revision_dir, modified_endpoint_names)
            print(f'{Colors.GREEN}[+]{Colors.END} Copied {copied_count} compatible corpus files to new revision')
        else:
            print(f'{Colors.YELLOW}[*]{Colors.END} Corpus copy disabled, new revision will start from scratch')

        print(f'{Colors.GREEN}[+]{Colors.END} New revision created: {next_revision}')

        # Clean up the old revision
        (workdir / 'fuzzer').unlink(missing_ok=True)
        (workdir / 'fuzzer.json').unlink(missing_ok=True)
        (workdir / 'fuzzer.pspec').unlink(missing_ok=True)
        (workdir / 'fuzzer.pspec.cache').unlink(missing_ok=True)

        # Safe stop point: we finished triage/patching work.
        if _stop_requested(stop_file):
            print(f'{Colors.YELLOW}[*]{Colors.END} Stop requested; exiting after completing triage/patching')
            _ack_stop(stop_file)
            return True

        return


def main(args):
    info_path = Path(args.info).absolute()
    harness = ProjectMetadata.model_validate_json(open(args.harness).read())
    campaign = Path(args.campaign).absolute()
    persist = Path(args.persist).absolute() if args.persist is not None else None
    stop_file = Path(args.stop_file).absolute() if getattr(args, 'stop_file', None) is not None else None

    if persist is None:
        exists = campaign.exists() and (campaign / 'reports').exists() and (campaign / 'revisions').exists()
    else:
        exists = (
            campaign.exists()
            and (campaign / 'revisions').exists()
            and (persist / 'reports').exists()
            and (persist / 'revisions').exists()
        )

    if exists and args.reset:
        print(f'{Colors.GREEN}[+]{Colors.END} Clearing old campaign: {campaign}')
        shutil.rmtree(campaign / 'revisions', ignore_errors=True)
        shutil.rmtree(campaign / 'scratch', ignore_errors=True)
        if persist is None:
            shutil.rmtree(campaign / 'reports', ignore_errors=True)
        else:
            shutil.rmtree(persist / 'reports', ignore_errors=True)
            shutil.rmtree(persist / 'buckets', ignore_errors=True)
            shutil.rmtree(persist / 'seeds', ignore_errors=True)

    if not exists or args.reset:
        print(f'{Colors.GREEN}[+]{Colors.END} Creating new campaign: {campaign}')
        campaign.mkdir(parents=True, exist_ok=True)
        if persist is None:
            (campaign / 'reports').mkdir(parents=True, exist_ok=True)
        else:
            (persist / 'reports').mkdir(parents=True, exist_ok=True)
            (persist / 'buckets').mkdir(parents=True, exist_ok=True)
            (persist / 'seeds').mkdir(parents=True, exist_ok=True)
            (persist / 'revisions').mkdir(parents=True, exist_ok=True)
        (campaign / 'revisions' / '0').mkdir(parents=True, exist_ok=True)
        # Copy the original harness to the campaign
        (campaign / 'revisions' / '0' / 'harness.json').write_text(harness.model_dump_json(indent=2))
        if persist is not None:
            # Persist the base revision so patch evolution can resume across restarts.
            (persist / 'revisions' / '0').mkdir(parents=True, exist_ok=True)
            (persist / 'revisions' / '0' / 'harness.json').write_text(harness.model_dump_json(indent=2))
    else:
        print(f'{Colors.GREEN}[+]{Colors.END} Using existing campaign: {campaign}')

    # Run the campaign
    for revision in range(args.max_revisions):
        print(f'{Colors.GREEN}[+]{Colors.END} Running campaign {revision + 1} of {args.max_revisions}')
        should_stop = run_campaign(
            campaign,
            info_path,
            revise=True,
            no_corpus_copy=args.no_corpus_copy,
            persist=persist,
            rotate_seconds=args.rotate_seconds,
            rotate_max_gb=args.rotate_max_gb,
            rotate_seed_keep=args.rotate_seed_keep,
            stop_file=stop_file,
        )
        if should_stop:
            return

    # Final run without revisions
    print(f'{Colors.GREEN}[+]{Colors.END} Running final campaign without revisions')
    run_campaign(
        campaign,
        info_path,
        revise=False,
        no_corpus_copy=args.no_corpus_copy,
        persist=persist,
        rotate_seconds=args.rotate_seconds,
        rotate_max_gb=args.rotate_max_gb,
        rotate_seed_keep=args.rotate_seed_keep,
        stop_file=stop_file,
    )


def register(subparser: ArgumentParser):
    parser = subparser.add_parser('looper')
    parser.add_argument('--info', type=str, default='info.json', help='Path to the project info')
    parser.add_argument('--harness', type=str, required=True, help='Path to the original harness')
    parser.add_argument('--campaign', type=str, required=True, help='Path to the campaign directory')
    parser.add_argument('--persist', type=str, default=None, help='Optional persistence root (reports/buckets/seeds). If set, campaign is treated as ephemeral workdir.')
    parser.add_argument('--stop-file', type=str, default=None, help='Optional stop-request file; when present, looper exits at safe points (between fuzzing and triage / between revisions).')
    parser.add_argument('--max-revisions', type=int, default=10, help='Maximum number of revisions to run')
    parser.add_argument('--reset', action='store_true', help='Reset the campaign (if it exists)')
    parser.add_argument('--no-corpus-copy', action='store_true', help='Do not copy compatible corpus files between revisions')
    parser.add_argument('--rotate-seconds', type=int, default=3600, help='Rotate ephemeral workdir every N seconds (minimal-persistence mode)')
    parser.add_argument('--rotate-max-gb', type=float, default=10.0, help='Rotate ephemeral workdir when it exceeds N GB (minimal-persistence mode)')
    parser.add_argument('--rotate-seed-keep', type=int, default=200, help='Keep newest N queue entries as seeds across rotations (minimal-persistence mode)')
    parser.set_defaults(func=main)
