
from pathlib import Path
import shutil

from .utils.colors import Colors
from .utils.project import Project, ProjectConfig


def get_repo_name(url):
    if url.startswith('https://github.com/'):
        org_name = url.split('/')[3]
        repo_name = url.split('/')[4].split('.git')[0]
        return f'{org_name}_{repo_name}'.lower()
    return None


def do_init(args):
    repo_name = get_repo_name(args.url)
    if repo_name is None:
        print(f'[{Colors.RED}-{Colors.END}] Invalid github url: {args.url}')
        return
   
    config = ProjectConfig(name=repo_name, repo=args.url, commit=args.commit)

    # Default to the repo name unless we have a custom path
    path = Path(args.path) if args.path is not None else Path(repo_name)

    project = Project(path)
    if project.exists():
        if args.force:
            shutil.rmtree(path)
        else:
            print(f'[{Colors.RED}-{Colors.END}] Project already exists. Use --force to overwrite')
            return
    
    project.create()
    project.set_config(config)
    print(f'[{Colors.GREEN}+{Colors.END}] Initialized project in {path}')


def register(subparsers):
    subparser = subparsers.add_parser('init', help='Initialize a new project')
    subparser.add_argument('url', help='The url of the project')
    subparser.add_argument('path', nargs='?', help='The path to the project', default=None)
    subparser.add_argument('-c', '--commit', help='The commit hash of the project (or most recent commit if not specified)')
    subparser.add_argument('-f', '--force', action='store_true', help='Overwrite the project if it already exists')
    subparser.set_defaults(func=do_init)
