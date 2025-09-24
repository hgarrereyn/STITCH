
from pathlib import Path
import shutil
import requests
import json

from .utils.colors import Colors
from .utils.project import Project, ProjectConfig


def get_repo_name(url):
    if url.startswith('https://github.com/'):
        org_name = url.split('/')[3]
        repo_name = url.split('/')[4].split('.git')[0]
        return f'{org_name}_{repo_name}'.lower()
    return None


def get_latest_commit(url):
    """
    Get the latest commit hash from a GitHub repository URL.
    This returns the commit that would be checked out by a simple git clone.
    """
    if not url.startswith('https://github.com/'):
        raise ValueError(f"Invalid GitHub URL: {url}")
    
    # Extract owner and repo from URL
    parts = url.rstrip('/').split('/')
    if len(parts) < 5:
        raise ValueError(f"Invalid GitHub URL format: {url}")
    
    owner = parts[3]
    repo = parts[4].replace('.git', '')
    
    # Use GitHub API to get the default branch's latest commit
    api_url = f"https://api.github.com/repos/{owner}/{repo}/commits"
    
    try:
        response = requests.get(api_url, params={'per_page': 1})
        response.raise_for_status()
        
        commits = response.json()
        if not commits:
            raise ValueError(f"No commits found in repository {owner}/{repo}")
        
        return commits[0]['sha']
        
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Failed to fetch latest commit from GitHub API: {e}")
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Unexpected response format from GitHub API: {e}")


def do_init(args):
    repo_name = get_repo_name(args.url)
    if repo_name is None:
        print(f'[{Colors.RED}-{Colors.END}] Invalid github url: {args.url}')
        return

    commit = args.commit if args.commit is not None else get_latest_commit(args.url)

    print(f'[{Colors.GREEN}+{Colors.END}] Pinned commit: {commit}')
   
    config = ProjectConfig(name=repo_name, repo=args.url, commit=commit)

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
