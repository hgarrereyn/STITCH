from pathlib import Path
import shutil
import tempfile

from .utils.env import check_env
from .utils.colors import Colors
from .utils.project import Project, Harness
from .data.project_info import ProjectInfo


def do_fuzz(args):
    path = Path(args.path)
    project = Project(path)
    if not project.exists() or not project.has_config():
        print(f'[{Colors.RED}-{Colors.END}] Could not find project at {path.absolute()}')
        return
    
    harness: Harness = None
    if args.name is None:
        all_harnesses = project.get_harnesses()
        if len(all_harnesses) == 0:
            print(f'[{Colors.RED}-{Colors.END}] No harnesses found')
            return
        if len(all_harnesses) == 1:
            harness = all_harnesses[0]
        else:
            print(f'[{Colors.RED}-{Colors.END}] Multiple harnesses found. Please specify one ({Colors.CYAN}--name <name>{Colors.END})')
            for harness in all_harnesses:
                print(f'  {Colors.CYAN}*{Colors.END} {harness.path.name}')
            return
    else:
        harness = project.get_harness(args.name)
    
    if not harness.exists():
        print(f'[{Colors.RED}-{Colors.END}] Harness {harness.name} does not exist')
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

        project.invoke(
            env=env,
            mounts=mounts, 
            cmd=f'''
                cd /fuzz/workspace &&
                agfi zephyr looper --harness harness.json --campaign /fuzz/campaign --max-revisions {args.max_revisions} {'--reset' if args.reset else ''}
            '''
        )


def register(subparsers):
    subparser = subparsers.add_parser('fuzz', help='Run the fuzzer')
    subparser.add_argument('path', nargs='?', help='The path to the project', default='.')
    subparser.add_argument('-n','--name', help='The name of the harness', default=None)
    subparser.add_argument('--max-revisions', type=int, help='The maximum number of revisions to run', default=30)
    subparser.add_argument('--no-asan', action='store_true', help='Do not use ASAN', default=False)
    subparser.add_argument('--reset', action='store_true', help='Reset the campaign', default=False)
    subparser.add_argument('--hard-rss-limit-mb', type=int, help='The hard RSS limit in MB', default=1024)
    subparser.set_defaults(func=do_fuzz)
