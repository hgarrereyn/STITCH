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
    
    project.build_docker()
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        tmpdir.mkdir(parents=True, exist_ok=True)
        shutil.copy(harness.harness_config(), tmpdir / 'harness.json')
        shutil.copy(project.path / 'config' / 'info.json', tmpdir / 'info.json')

        mounts = [
            (str(tmpdir.absolute()), '/fuzz/workspace'),
            (str(harness.output_dir().absolute()), '/fuzz/output'),
        ]
        project.invoke(
            mounts=mounts, 
            cmd='''
                cd /fuzz/workspace &&
                agfi flow to-schema --meta harness.json --output test.json &&
                agfi build full test &&
                mkdir -p /fuzz/input &&
                ./test -i /fuzz/input -o /fuzz/output --bypass-validation
            ''')


def register(subparsers):
    subparser = subparsers.add_parser('fuzz', help='Run the fuzzer')
    subparser.add_argument('path', nargs='?', help='The path to the project', default='.')
    subparser.add_argument('--name', help='The name of the harness', default=None)
    subparser.set_defaults(func=do_fuzz)
