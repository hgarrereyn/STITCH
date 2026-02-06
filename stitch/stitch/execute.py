from pathlib import Path
import shutil
import tempfile

from .utils.colors import Colors
from .utils.project import Project, Harness

def do_exec(args):
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

    testcase_path = Path(args.testcase)
    if not testcase_path.exists():
        print(f'[{Colors.RED}-{Colors.END}] Testcase {testcase_path} does not exist')
        return
    
    asan = not args.no_asan
    if asan:
        print(f'[{Colors.GREEN}+{Colors.END}] Using ASAN')

    image_tag = project.build_docker(use_asan=asan, override_commit=args.commit)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        tmpdir.mkdir(parents=True, exist_ok=True)
        shutil.copy(harness.harness_config(), tmpdir / 'harness.json')
        shutil.copy(project.path / 'config' / 'info.json', tmpdir / 'info.json')
        shutil.copy(testcase_path, tmpdir / 'testcase.json')

        mounts = [
            (str(tmpdir.absolute()), '/fuzz/workspace'),
            (str(harness.campaign_dir().absolute()), '/fuzz/campaign'),
        ]

        cmd = f'''
            cd /fuzz/workspace &&
            stitchi zephyr to-schema --meta harness.json --output fuzzer.json &&
            stitchi build full fuzzer &&
            ./fuzzer -e testcase.json
        '''

        project.invoke(
            mounts=mounts, 
            image=image_tag,
            cmd=cmd
        )


def register(subparsers):
    subparser = subparsers.add_parser('exec', help='Execute a raw testcase with the fuzzer')
    subparser.add_argument('path', nargs='?', help='The path to the project', default='.')
    subparser.add_argument('-t', '--testcase', type=str, required=True, help='The path to the raw testcase (json)')
    subparser.add_argument('-n','--name', help='The name of the harness', default=None)
    subparser.add_argument('-c', '--commit', type=str, help='Test on a specific commit', default=None)
    subparser.add_argument('--no-asan', action='store_true', help='Do not use ASAN', default=False)
    subparser.set_defaults(func=do_exec)
