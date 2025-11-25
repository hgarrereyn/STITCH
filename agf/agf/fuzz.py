from pathlib import Path
import shutil
import tempfile
from typing import Optional

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

    project.build_docker(use_asan=asan, hard_rss_limit_mb=args.hard_rss_limit_mb)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        tmpdir.mkdir(parents=True, exist_ok=True)
        shutil.copy(harness.harness_config(), tmpdir / 'harness.json')
        shutil.copy(project.path / 'config' / 'info.json', tmpdir / 'info.json')

        mounts = [
            (str(tmpdir.absolute()), '/fuzz/workspace'),
            (str(harness.campaign_dir().absolute()), '/fuzz/campaign'),
        ]

        env = [
            'OPENAI_API_KEY',
        ]

        if args.debug:
            import os
            os.environ['AGF_DEBUG_VIZ'] = '1'
            env.append('AGF_DEBUG_VIZ')
            print(
                f'[{Colors.GREEN}+{Colors.END}] Debug mode enabled. '
                f'Run {Colors.CYAN}agf monitor{Colors.END} to visualize the process.'
            )

        project.invoke(
            env=env,
            mounts=mounts,
            cmd=f'''
                cd /fuzz/workspace &&
                agfi zephyr looper --harness harness.json --campaign /fuzz/campaign --max-revisions {args.max_revisions} {'--reset' if args.reset else ''} {'--no-corpus-copy' if args.no_corpus_copy else ''}
            ''',
        )


def register(subparsers):
    # High-level fuzz command (looper + bucketizer + patching)
    subparser = subparsers.add_parser('fuzz', help='Run the fuzzer with automatic crash triage and patching')
    subparser.add_argument('path', nargs='?', help='The path to the project', default='.')
    subparser.add_argument('-n', '--name', help='The name of the harness', default=None)
    subparser.add_argument('--max-revisions', type=int, help='The maximum number of revisions to run', default=30)
    subparser.add_argument('--no-asan', action='store_true', help='Do not use ASAN', default=False)
    subparser.add_argument('--reset', action='store_true', help='Reset the campaign', default=False)
    subparser.add_argument('--hard-rss-limit-mb', type=int, help='The hard RSS limit in MB', default=1024)
    subparser.add_argument('--debug', action='store_true', help='Enable debug visualization', default=False)
    subparser.add_argument(
        '--no-corpus-copy',
        action='store_true',
        help='Do not copy compatible corpus files between revisions',
        default=False,
    )
    subparser.set_defaults(func=do_fuzz)
