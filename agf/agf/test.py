from pathlib import Path
import tempfile
import shutil

from .utils.colors import Colors
from .utils.project import Project, Harness


def do_test(args):
    path = Path(args.path)
    project = Project(path)
    if not project.exists() or not project.has_config():
        print(f'[{Colors.RED}-{Colors.END}] Could not find project at {path.absolute()}')
        return

    project.build_docker()
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        tmpdir.mkdir(parents=True, exist_ok=True)
        shutil.copy(project.path / 'config' / 'info.json', tmpdir / 'info.json')
        shutil.copy(project.path / 'config' / 'test.cpp', tmpdir / 'test.cpp')

        mounts = [
            (str(tmpdir.absolute()), '/fuzz/workspace'),
        ]
        project.invoke(
            mounts=mounts, 
            cmd='''
                cd /fuzz/workspace &&
                agfi build external test &&
                ./test
            ''')

def register(subparsers):
    subparser = subparsers.add_parser('test', help='Test the project build configuration')
    subparser.add_argument('path', nargs='?', help='The path to the project', default='.')
    subparser.set_defaults(func=do_test)
