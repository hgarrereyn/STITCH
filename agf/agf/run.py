from pathlib import Path
import shutil
import tempfile

from .utils.colors import Colors
from .utils.project import Project


def do_bug(args):
    path = Path(args.path)
    project = Project(path)
    if not project.exists() or not project.has_config():
        print(f'[{Colors.RED}-{Colors.END}] Could not find project at {path.absolute()}')
        return

    asan = not args.no_asan
    if asan:
        print(f'[{Colors.GREEN}+{Colors.END}] Using ASAN')

    project.build_docker(use_asan=asan, override_commit=args.commit)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        tmpdir.mkdir(parents=True, exist_ok=True)
        shutil.copy(project.path / 'config' / 'info.json', tmpdir / 'info.json')
        shutil.copy(args.testcase, tmpdir / 'test.cpp')

        mounts = [
            (str(tmpdir.absolute()), '/fuzz/workspace'),
        ]

        if args.debug:
            cmd = f'''
                cd /fuzz/workspace &&
                agfi build external test &&
                pwndbg ./test
            '''
        else:
            cmd = f'''
                cd /fuzz/workspace &&
                agfi build external test &&
                ./test
            '''

        project.invoke(
            mounts=mounts, 
            cmd=cmd
        )


def register(subparsers):
    subparser = subparsers.add_parser('bug', help='Compile and run a provided testcase')
    subparser.add_argument('path', nargs='?', help='The path to the project', default='.')
    subparser.add_argument('-t', '--testcase', type=str, help='The path to the testcase (.cpp)', default=None)
    subparser.add_argument('-c', '--commit', type=str, help='Test on a specific commit', default=None)
    subparser.add_argument('--no-asan', action='store_true', help='Do not use ASAN', default=False)
    subparser.add_argument('-d', '--debug', action='store_true', help='Spawn a shell', default=False)
    subparser.set_defaults(func=do_bug)
