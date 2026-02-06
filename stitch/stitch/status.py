from pathlib import Path
import json

from .utils.colors import Colors
from .utils.project import Project


def do_status_set(args):
    """Set a key-value pair in the status JSON."""
    path = Path(args.path)
    project = Project(path)
    if not project.exists() or not project.has_config():
        print(f'[{Colors.RED}-{Colors.END}] Could not find project at {path.absolute()}')
        return
    
    status_path = project.status_file()
    status = json.loads(status_path.read_text())
    
    status[args.key] = args.value
    
    status_path.write_text(json.dumps(status, indent=2))
    print(f'[{Colors.GREEN}+{Colors.END}] Set {args.key} = {args.value}')


def do_status_get(args):
    """Get a value from the status JSON."""
    path = Path(args.path)
    project = Project(path)
    if not project.exists() or not project.has_config():
        print(f'[{Colors.RED}-{Colors.END}] Could not find project at {path.absolute()}')
        return
    
    status_path = project.status_file()
    status = json.loads(status_path.read_text())
    
    if args.key in status:
        print(status[args.key])
    else:
        print(f'[{Colors.YELLOW}!{Colors.END}] Key "{args.key}" not found')
        return 1


def do_status_list(args):
    """List all key-value pairs in the status JSON."""
    path = Path(args.path)
    project = Project(path)
    if not project.exists() or not project.has_config():
        print(f'[{Colors.RED}-{Colors.END}] Could not find project at {path.absolute()}')
        return
    
    status_path = project.status_file()
    status = json.loads(status_path.read_text())
    
    if not status:
        print(f'[{Colors.DIM}No status data{Colors.END}]')
        return
    
    for key, value in sorted(status.items()):
        print(f'{key}: {value}')


def register(subparsers):
    parser = subparsers.add_parser('status', help='Manage status key-value pairs')
    
    # Create subparsers for subcommands
    subparsers_status = parser.add_subparsers(dest='status_command', help='Status subcommand', required=True)
    
    # Set subcommand
    parser_set = subparsers_status.add_parser('set', help='Set a key-value pair')
    parser_set.add_argument('key', help='The key')
    parser_set.add_argument('value', help='The value')
    parser_set.add_argument('path', nargs='?', help='The path to the project', default='.')
    parser_set.set_defaults(status_subparser=do_status_set)
    
    # Get subcommand
    parser_get = subparsers_status.add_parser('get', help='Get a value by key')
    parser_get.add_argument('key', help='The key')
    parser_get.add_argument('path', nargs='?', help='The path to the project', default='.')
    parser_get.set_defaults(status_subparser=do_status_get)
    
    # List subcommand
    parser_list = subparsers_status.add_parser('list', help='List all key-value pairs')
    parser_list.add_argument('path', nargs='?', help='The path to the project', default='.')
    parser_list.set_defaults(status_subparser=do_status_list)
    
    def do_status(args):
        if hasattr(args, 'status_subparser'):
            args.status_subparser(args)
        else:
            parser.print_help()
    
    parser.set_defaults(func=do_status)
