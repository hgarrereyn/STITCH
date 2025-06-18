
from pathlib import Path
import shutil

from .utils.colors import Colors
from .utils.project import Project, ProjectConfig


def get_repo_name(url):
    # Ensure its a github url and get the repo name
    if not url.startswith('https://github.com/'):
        return None
    last_part = url.split('/')[-1]
    if last_part.endswith('.git'):
        return last_part[:-4]
    return last_part


def do_init(args):
    path = args.path
    if path is None:
        repo_name = get_repo_name(args.url)
        if repo_name is None:
            print(f'[{Colors.RED}-{Colors.END}] Invalid github url: {args.url}')
            return
        path = repo_name

    path = Path(path)
    project = Project(path)
    if project.exists():
        if args.force:
            shutil.rmtree(path)
        else:
            print(f'[{Colors.RED}-{Colors.END}] Project already exists. Use --force to overwrite')
            return
    
    project.create()
    project.set_config(ProjectConfig(repo=args.url))
    print(f'[{Colors.GREEN}+{Colors.END}] Initialized project in {path}')


def register(subparsers):
    subparser = subparsers.add_parser('init', help='Initialize a new project')
    subparser.add_argument('url', help='The url of the project')
    subparser.add_argument('path', nargs='?', help='The path to the project', default=None)
    subparser.add_argument('-f', '--force', action='store_true', help='Overwrite the project if it already exists')
    subparser.set_defaults(func=do_init)
