
from pathlib import Path
import shutil
import json
import tempfile
import subprocess
import sys

from .utils.colors import Colors


def get_repo_name(url):
    # https://github.com/chromium/chromium -> chromium_chromium
    if url.startswith('https://github.com/'):
        org_name = url.split('/')[3]
        repo_name = url.split('/')[4].split('.git')[0]
        return f'{org_name}_{repo_name}'.lower().replace('-', '_')
    # https://chromium.googlesource.com/chromiumos/third_party/libdrm -> chromiumos_third_party_libdrm
    if url.startswith('https://chromium.googlesource.com/'):
        parts = url.split('https://chromium.googlesource.com/')[1].rstrip('/').split('/')
        return '_'.join(parts).replace('-', '_').lower()
    
    # otherwise, convert the whole url into a name
    # https://aomedia.googlesource.com/aom -> aomedia_googlesource_com_aom
    parts = url.split('//')[1].rstrip('/').split('/')
    return '_'.join(parts).replace('.', '_').replace('-', '_').lower()


def get_latest_commit(url, *, quiet: bool = False):
    # TODO: rate-limiting issues in bulk requests
    # if url.startswith('https://github.com/'):
    #     return get_latest_commit_github(url)
    return get_latest_commit_clone(url, quiet=quiet)


def get_latest_commit_github(url):
    """
    Get the latest commit hash from a GitHub repository URL.
    This returns the commit that would be checked out by a simple git clone.
    """
    # Lazy import so `stitch` can start up even in restricted environments
    # (and to avoid paying import cost when the user provides --commit).
    import requests

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


def get_latest_commit_clone(url, *, quiet: bool = False):
    """
    Get the latest commit hash from a clone of a repository.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        tmpdir.mkdir(parents=True, exist_ok=True)
        # `git clone` writes progress to stderr by default, which breaks `-q`
        # mode. Suppress it when requested.
        if quiet:
            subprocess.run(
                ['git', 'clone', '--depth', '1', url, tmpdir],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            subprocess.run(['git', 'clone', '--depth', '1', url, tmpdir], check=True)

        # figure out what commit we are on
        return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=tmpdir).decode('utf-8').strip()


def do_init(args):
    quiet = getattr(args, 'quiet', False)
    # Lazily import heavier dependencies so `stitch init -h` (and other CLI
    # introspection) stays fast and works in more restricted environments.
    from .utils.project import Project, ProjectConfig

    def fail(msg: str):
        # Quiet mode is intended for scripting: keep stdout clean and use a
        # non-zero exit code on failure.
        if quiet:
            print(msg, file=sys.stderr)
            raise SystemExit(1)
        print(f'[{Colors.RED}-{Colors.END}] {msg}')
        return

    repo_name = get_repo_name(args.url)
    if repo_name is None:
        return fail(f'Invalid url: {args.url}')

    try:
        commit = args.commit if args.commit is not None else get_latest_commit(args.url, quiet=quiet)
    except Exception as e:
        return fail(f'Failed to determine latest commit: {e}')

    if not quiet:
        print(f'[{Colors.GREEN}+{Colors.END}] Pinned commit: {commit}')
   
    model = getattr(args, 'model', None)
    config = ProjectConfig(name=repo_name, repo=args.url, commit=commit, model=model)

    # Default to the repo name unless we have a custom path
    path = Path(args.path) if args.path is not None else Path(repo_name)

    project = Project(path)
    if project.exists():
        if args.force:
            shutil.rmtree(path)
        else:
            return fail('Project already exists. Use --force to overwrite')
    
    project.create()
    project.set_config(config)

    if quiet:
        # Print only the full path to the new project directory (for scripts).
        print(str(project.path.resolve()))
    else:
        print(f'[{Colors.GREEN}+{Colors.END}] Initialized project in {path}')


def register(subparsers):
    subparser = subparsers.add_parser('init', help='Initialize a new project')
    subparser.add_argument('url', help='The url of the project')
    subparser.add_argument('path', nargs='?', help='The path to the project', default=None)
    subparser.add_argument('-c', '--commit', help='The commit hash of the project (or most recent commit if not specified)')
    subparser.add_argument('-f', '--force', action='store_true', help='Overwrite the project if it already exists')
    subparser.add_argument(
        '-q',
        '--quiet',
        action='store_true',
        help='Only print the full path to the initialized project directory (for scripting)',
    )
    subparser.add_argument(
        '-m',
        '--model',
        type=str,
        default=None,
        help='The default LLM model to use for inference tasks (can be overridden per-command)',
    )
    subparser.set_defaults(func=do_init)
