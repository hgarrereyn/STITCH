from pathlib import Path
import tempfile
import shutil

from .utils.colors import Colors
from .utils.project import Project, Harness


def do_infer(args):
    path = Path(args.path)
    project = Project(path)
    if not project.exists() or not project.has_config():
        print(f'[{Colors.RED}-{Colors.END}] Could not find project at {path.absolute()}')
        return
    
    harness: Harness = None
    if not args.base:
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

    project.build_docker(args.coverage)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        tmpdir.mkdir(parents=True, exist_ok=True)
        if not args.base:
            shutil.copy(harness.harness_config(), tmpdir / 'harness.json')
        shutil.copy(project.path / 'config' / 'info.json', tmpdir / 'info.json')

        mounts = [
            (str(tmpdir.absolute()), '/fuzz/workspace')
        ]
        if not args.base:
            mounts.append((str(harness.output_dir().absolute()), '/fuzz/output'))
            if args.coverage:
                mounts.append((str(harness.coverage_dir().absolute()), '/fuzz/coverage'))

        if args.base:
            project.invoke(
                mounts=mounts, 
                cmd='''
                cd /fuzz/workspace &&
                bash
                '''
            )
        else:
            project.invoke(
                mounts=mounts, 
                cmd='''
                    cd /fuzz/workspace &&
                    agfi flow to-schema --meta harness.json --output test.json &&
                    agfi build full test &&
                    bash
                ''')

def register(subparsers):
    subparser = subparsers.add_parser('dev', help='Launch a shell inside the main container')
    subparser.add_argument('path', nargs='?', help='The path to the project', default='.')
    subparser.add_argument('--name', help='The name of the harness', default=None)
    subparser.add_argument('--base', action='store_true', help='Use the base image')
    subparser.add_argument('--coverage', action='store_true', help='Build the base image with coverage instrumentation')
    subparser.set_defaults(func=do_infer)
