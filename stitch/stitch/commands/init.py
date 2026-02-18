from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

from .._colors import Colors, ok, err, fatal


def _is_local_path(source: str) -> bool:
    """Heuristic: treat as a local path if it looks like a filesystem path."""
    if source.startswith(("http://", "https://", "git://", "ssh://", "git@")):
        return False
    expanded = os.path.expanduser(source)
    if os.path.exists(expanded):
        return True
    if source.startswith(("/", "~", "./", "../")):
        return True
    return False


def _get_repo_name(url: str) -> str:
    if url.startswith("https://github.com/"):
        org = url.split("/")[3]
        repo = url.split("/")[4].split(".git")[0]
        return f"{org}_{repo}".lower().replace("-", "_")
    if url.startswith("https://chromium.googlesource.com/"):
        parts = url.split("https://chromium.googlesource.com/")[1].rstrip("/").split("/")
        return "_".join(parts).replace("-", "_").lower()
    parts = url.split("//")[1].rstrip("/").split("/")
    return "_".join(parts).replace(".", "_").replace("-", "_").lower()


def _get_local_name(local_path: Path) -> str:
    return local_path.name.lower().replace("-", "_").replace(".", "_")


def _get_latest_commit(url: str, *, quiet: bool = False) -> str:
    with tempfile.TemporaryDirectory() as tmpdir:
        kw = dict(check=True)
        if quiet:
            kw.update(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "clone", "--depth", "1", url, tmpdir], **kw)
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=tmpdir
        ).decode("utf-8").strip()


def _get_local_commit(local_path: Path) -> str | None:
    """If the local path is a git repo, return the current HEAD commit."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=local_path,
            stderr=subprocess.DEVNULL,
        ).decode("utf-8").strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def do_init(args):
    quiet = getattr(args, "quiet", False)

    from .._project import Project, ProjectConfig

    source = args.source

    if _is_local_path(source):
        resolved = Path(os.path.expanduser(source)).resolve()
        if not resolved.is_dir():
            fatal(f"Local path does not exist or is not a directory: {resolved}")

        project_name = _get_local_name(resolved)
        commit = args.commit or _get_local_commit(resolved)

        if not quiet and commit:
            ok(f"Detected git commit: {commit}")

        model = getattr(args, "model", None)
        config = ProjectConfig(
            name=project_name,
            local_path=str(resolved),
            commit=commit,
            model=model,
        )

        path = Path(args.path) if args.path is not None else Path(project_name)
    else:
        repo_name = _get_repo_name(source)

        try:
            commit = args.commit if args.commit is not None else _get_latest_commit(source, quiet=quiet)
        except Exception as e:
            fatal(f"Failed to determine latest commit: {e}")

        if not quiet:
            ok(f"Pinned commit: {commit}")

        model = getattr(args, "model", None)
        config = ProjectConfig(name=repo_name, repo=source, commit=commit, model=model)

        path = Path(args.path) if args.path is not None else Path(repo_name)

    project = Project(path)
    if project.exists():
        is_empty = not any(path.iterdir())
        if args.force:
            shutil.rmtree(path)
        elif not is_empty:
            fatal("Project already exists. Use --force to overwrite")

    project.create()
    project.set_config(config)

    if quiet:
        print(str(project.path.resolve()))
    else:
        ok(f"Initialized project in {path}")


def register(subparsers):
    p = subparsers.add_parser("init", help="Initialize a new project from a git repository or local path")
    p.add_argument("source", help="Repository URL or local path to source directory")
    p.add_argument("path", nargs="?", default=None, help="Local project directory (defaults to repo/dir name)")
    p.add_argument("-c", "--commit", help="Pin a specific commit (default: latest)")
    p.add_argument("-f", "--force", action="store_true", help="Overwrite existing project")
    p.add_argument("-q", "--quiet", action="store_true", help="Script-friendly output (prints path only)")
    p.add_argument("-m", "--model", help="Default LLM model for this project")
    p.set_defaults(func=do_init)
