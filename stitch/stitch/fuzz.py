from pathlib import Path
import shutil
import tempfile
from typing import Optional
import os
import signal
import subprocess
import time

from .utils.env import check_env
from .utils.colors import Colors
from .utils.project import Project, Harness
from .data.project_info import ProjectInfo


def _select_harness(project: Project, name: Optional[str]) -> Optional[Harness]:
    """Shared harness selection logic."""
    harness: Optional[Harness] = None
    if name is None:
        all_harnesses = project.get_harnesses()
        if len(all_harnesses) == 0:
            print(f'[{Colors.RED}-{Colors.END}] No harnesses found')
            return None
        if len(all_harnesses) == 1:
            harness = all_harnesses[0]
        else:
            print(
                f'[{Colors.RED}-{Colors.END}] Multiple harnesses found. '
                f'Please specify one ({Colors.CYAN}--name <name>{Colors.END})'
            )
            for h in all_harnesses:
                print(f'  {Colors.CYAN}*{Colors.END} {h.path.name}')
            return None
    else:
        harness = project.get_harness(name)

    if not harness.exists():
        print(f'[{Colors.RED}-{Colors.END}] Harness {name} does not exist')
        return None

    return harness


def do_fuzz(args):
    path = Path(args.path)
    project = Project(path)
    if not project.exists() or not project.has_config():
        print(f'[{Colors.RED}-{Colors.END}] Could not find project at {path.absolute()}')
        return

    harness = _select_harness(project, args.name)
    if harness is None:
        return

    asan = not args.no_asan
    if asan:
        print(f'[{Colors.GREEN}+{Colors.END}] Using ASAN')

    check_env(['OPENAI_API_KEY'])

    image_tag = project.build_docker(use_asan=asan, hard_rss_limit_mb=args.hard_rss_limit_mb)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        tmpdir.mkdir(parents=True, exist_ok=True)
        shutil.copy(harness.harness_config(), tmpdir / 'harness.json')
        shutil.copy(project.path / 'config' / 'info.json', tmpdir / 'info.json')

        # Minimal persistence mode:
        # - Keep fuzzing state (revisions/corpus/crashes/scratch) in the container FS
        #   so it can be wiped/reset without touching the host.
        # - Persist only small, high-value artifacts on the host.
        host_campaign = harness.campaign_dir().absolute()
        reports_dir = host_campaign / 'reports'
        buckets_dir = host_campaign / 'buckets'
        seeds_dir = host_campaign / 'seeds'
        revisions_dir = host_campaign / 'revisions'
        control_dir = host_campaign / 'control'
        reports_dir.mkdir(parents=True, exist_ok=True)
        buckets_dir.mkdir(parents=True, exist_ok=True)
        seeds_dir.mkdir(parents=True, exist_ok=True)
        revisions_dir.mkdir(parents=True, exist_ok=True)
        control_dir.mkdir(parents=True, exist_ok=True)
        stop_file = control_dir / 'stop'

        mounts = [
            (str(tmpdir.absolute()), '/fuzz/workspace'),
            (str(reports_dir), '/fuzz/persist/reports'),
            (str(buckets_dir), '/fuzz/persist/buckets'),
            (str(seeds_dir), '/fuzz/persist/seeds'),
            # Minimal persistence also stores small per-revision harness.json snapshots
            # under persist/revisions so patch evolution can resume across restarts.
            (str(revisions_dir), '/fuzz/persist/revisions'),
            # Control files (e.g. safe-stop requests) persisted on the host.
            (str(control_dir), '/fuzz/persist/control'),
        ]

        env = [
            'OPENAI_API_KEY',
        ]

        # Graceful shutdown:
        # - First Ctrl+C: request a safe stop via the stop-file and keep waiting.
        # - Second Ctrl+C: force-stop the docker run (kills the container).
        sigint_count = 0
        force_exit = False

        def _on_sigint(_signum, _frame):
            nonlocal sigint_count, force_exit
            sigint_count += 1
            if sigint_count == 1:
                try:
                    stop_file.parent.mkdir(parents=True, exist_ok=True)
                    stop_file.touch(exist_ok=True)
                except Exception:
                    pass
                print(
                    f'[{Colors.YELLOW}[*]{Colors.END}] '
                    f'Graceful stop requested (created {stop_file}). '
                    f'Press Ctrl+C again to force quit.'
                )
            else:
                force_exit = True
                print(f'[{Colors.RED}-{Colors.END}] Force quitting (second Ctrl+C)...')

        old_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, _on_sigint)

        p = project.invoke(
            env=env,
            mounts=mounts,
            image=image_tag,
            cmd=f'''
                cd /fuzz/workspace &&
                mkdir -p /fuzz/persist/reports /fuzz/persist/buckets /fuzz/persist/seeds /fuzz/persist/revisions &&
                export PYTHONUNBUFFERED=1 &&
                (
                  command -v stdbuf >/dev/null 2>&1 &&
                  exec stdbuf -oL -eL stitchi zephyr looper --harness harness.json --campaign /tmp/stitch_campaign --persist /fuzz/persist --stop-file /fuzz/persist/control/stop --max-revisions {args.max_revisions} {'--reset' if args.reset else ''} {'--no-corpus-copy' if args.no_corpus_copy else ''}
                ) || (
                  exec stitchi zephyr looper --harness harness.json --campaign /tmp/stitch_campaign --persist /fuzz/persist --stop-file /fuzz/persist/control/stop --max-revisions {args.max_revisions} {'--reset' if args.reset else ''} {'--no-corpus-copy' if args.no_corpus_copy else ''}
                )
            ''',
            popen=True,
            # We intentionally disable TTY so we can put `docker run` into its own
            # process group without risking background TTY reads.
            use_tty=False,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
        )

        try:
            # Poll so the second Ctrl+C can force-terminate the process group.
            while True:
                rc = p.poll()
                if rc is not None:
                    return rc

                if force_exit:
                    try:
                        os.killpg(p.pid, signal.SIGTERM)
                    except Exception:
                        pass
                    # Give docker a moment to clean up before SIGKILL.
                    deadline = time.time() + 2.0
                    while time.time() < deadline and p.poll() is None:
                        time.sleep(0.05)
                    if p.poll() is None:
                        try:
                            os.killpg(p.pid, signal.SIGKILL)
                        except Exception:
                            pass
                    # Wait for exit (best-effort)
                    try:
                        p.wait(timeout=5.0)
                    except Exception:
                        pass
                    return

                time.sleep(0.2)
        finally:
            signal.signal(signal.SIGINT, old_handler)


def register(subparsers):
    # High-level fuzz command (looper + bucketizer + patching)
    subparser = subparsers.add_parser('fuzz', help='Run the fuzzer with automatic crash triage and patching')
    subparser.add_argument('path', nargs='?', help='The path to the project', default='.')
    subparser.add_argument('-n', '--name', help='The name of the harness', default=None)
    subparser.add_argument('--max-revisions', type=int, help='The maximum number of revisions to run', default=30)
    subparser.add_argument('--no-asan', action='store_true', help='Do not use ASAN', default=False)
    subparser.add_argument('--reset', action='store_true', help='Reset the campaign', default=False)
    subparser.add_argument('--hard-rss-limit-mb', type=int, help='The hard RSS limit in MB', default=1024)
    subparser.add_argument(
        '--no-corpus-copy',
        action='store_true',
        help='Do not copy compatible corpus files between revisions',
        default=False,
    )
    subparser.set_defaults(func=do_fuzz)
