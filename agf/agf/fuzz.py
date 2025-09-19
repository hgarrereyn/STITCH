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
    
    if args.asan:
        print(f'[{Colors.GREEN}+{Colors.END}] Using ASAN')

    project.build_docker(use_asan=args.asan)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        tmpdir.mkdir(parents=True, exist_ok=True)
        shutil.copy(harness.harness_config(), tmpdir / 'harness.json')
        shutil.copy(project.path / 'config' / 'info.json', tmpdir / 'info.json')

        output_dir = harness.asan_output_dir() if args.asan else harness.output_dir()
        mounts = [
            (str(tmpdir.absolute()), '/fuzz/workspace'),
            (str(output_dir.absolute()), '/fuzz/output'),
        ]

        runner_pre = f"timeout -k 10s {args.time} sh -c '" if args.time is not None else "sh -c '"
        runner_post = "'"

        project.invoke(
            mounts=mounts, 
            cmd=f'''
                cd /fuzz/workspace &&
                agfi inc to-schema --meta harness.json --output test.json &&
                agfi build full test &&
                unset LD_LIBRARY_PATH &&
                mkdir -p /fuzz/input &&
                export LD_LIBRARY_PATH=/fuzz/install/lib &&
                {runner_pre}trap "echo [INFO] Caught interrupt, exiting...; exit 130" INT TERM; while true; do
                    echo "[INFO] Starting fuzzer..."
                    ./test -i /fuzz/input -o /fuzz/output --bypass-validation --logfile /fuzz/output/log.txt --rss-limit-mb {args.rss_limit_mb if args.rss_limit_mb is not None else '0'} {'--keep-stdout' if args.keep_stdout else ''}
                    exit_code=$?
                    echo "[INFO] Fuzzer crashed with exit code $exit_code, restarting..."
                done
                {runner_post}
            ''')


def register(subparsers):
    subparser = subparsers.add_parser('fuzz', help='Run the fuzzer')
    subparser.add_argument('path', nargs='?', help='The path to the project', default='.')
    subparser.add_argument('-n','--name', help='The name of the harness', default=None)
    subparser.add_argument('-t', '--time', type=int, help='The time to run the fuzzer for (in seconds)', default=None)
    subparser.add_argument('-v', '--verbose', action='store_true', help='Verbose output', default=False)
    subparser.add_argument('--no-asan', action='store_true', help='Do not use ASAN', default=False)
    subparser.set_defaults(func=do_fuzz)
