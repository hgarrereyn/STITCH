from pathlib import Path
import shutil

from .utils.colors import Colors
from .utils.project import Project, Harness


def get_dir_size(path: Path) -> int:
    """Calculate the total size of a directory in bytes."""
    total = 0
    try:
        for entry in path.rglob('*'):
            if entry.is_file():
                total += entry.stat().st_size
    except Exception:
        pass
    return total


def format_size(size_bytes: int) -> str:
    """Format bytes into human-readable size."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"


def do_clear(args):
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
        print(f'[{Colors.RED}-{Colors.END}] Harness {harness.path.name} does not exist')
        return
    
    # Get campaign directory
    campaign_dir = harness.path / 'campaign'
    
    if not campaign_dir.exists():
        print(f'[{Colors.YELLOW}!{Colors.END}] No campaign found for harness {Colors.CYAN}{harness.path.name}{Colors.END}')
        return
    
    # Calculate size
    size_bytes = get_dir_size(campaign_dir)
    size_str = format_size(size_bytes)
    
    print(f'[{Colors.YELLOW}!{Colors.END}] About to delete campaign for harness {Colors.CYAN}{harness.path.name}{Colors.END}')
    print(f'[{Colors.YELLOW}!{Colors.END}] Campaign size: {Colors.LIGHT_YELLOW}{size_str}{Colors.END}')
    
    # Confirm deletion unless force flag is set
    if not args.force:
        try:
            response = input(f'[{Colors.RED}?{Colors.END}] Are you sure you want to delete this campaign? (yes/no): ')
            if response.lower() not in ['yes', 'y']:
                print(f'[{Colors.YELLOW}!{Colors.END}] Operation cancelled')
                return
        except (KeyboardInterrupt, EOFError):
            print(f'\n[{Colors.YELLOW}!{Colors.END}] Operation cancelled')
            return
    
    # Delete the campaign directory
    try:
        shutil.rmtree(campaign_dir)
        print(f'[{Colors.GREEN}+{Colors.END}] Successfully deleted campaign ({size_str} freed)')
    except Exception as e:
        print(f'[{Colors.RED}-{Colors.END}] Failed to delete campaign: {e}')


def register(subparsers):
    subparser = subparsers.add_parser('clear', help='Delete the campaign for a fuzzer')
    subparser.add_argument('path', nargs='?', help='The path to the project', default='.')
    subparser.add_argument('-n', '--name', help='The name of the harness', default=None)
    subparser.add_argument('-f', '--force', action='store_true', help='Skip confirmation prompt', default=False)
    subparser.set_defaults(func=do_clear)
