from pathlib import Path
import shutil

from .._colors import Colors, ok, warn, fatal
from .._harness import select_harness


def _format_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"


def _dir_size(path: Path) -> int:
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                total += entry.stat().st_size
    except Exception:
        pass
    return total


def do_clear(args):
    from .._project import Project

    path = Path(args.path)
    project = Project(path)
    if not project.exists() or not project.has_config():
        fatal(f"Could not find project at {path.absolute()}")

    harness = select_harness(project, args.name)
    if harness is None:
        return

    campaign_dir = harness.path / "campaign"
    if not campaign_dir.exists():
        warn(f"No campaign found for harness {Colors.CYAN}{harness.path.name}{Colors.END}")
        return

    size = _dir_size(campaign_dir)
    size_str = _format_size(size)

    warn(f"About to delete campaign for harness {Colors.CYAN}{harness.path.name}{Colors.END}")
    warn(f"Campaign size: {Colors.YELLOW}{size_str}{Colors.END}")

    if not args.force:
        try:
            ans = input(f"[{Colors.RED}?{Colors.END}] Delete campaign? (yes/no): ")
            if ans.strip().lower() not in ("yes", "y"):
                warn("Cancelled")
                return
        except (KeyboardInterrupt, EOFError):
            print()
            warn("Cancelled")
            return

    try:
        shutil.rmtree(campaign_dir)
        ok(f"Deleted campaign ({size_str} freed)")
    except Exception as e:
        fatal(f"Failed to delete campaign: {e}")


def register(subparsers):
    p = subparsers.add_parser("clear", help="Delete campaign data for a harness")
    p.add_argument("path", nargs="?", default=".", help="Project directory")
    p.add_argument("-n", "--name", help="Harness name")
    p.add_argument("-f", "--force", action="store_true", help="Skip confirmation prompt")
    p.set_defaults(func=do_clear)
