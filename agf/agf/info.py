from pathlib import Path

from .utils.colors import Colors
from .utils.project import Project


def do_info(args):
    path = Path(args.path)
    project = Project(path)
    if not project.exists() or not project.has_config():
        print(f'[{Colors.RED}-{Colors.END}] Could not find project at {path.absolute()}')
        return

    config = project.get_config()
    print(f'[{Colors.GREEN}+{Colors.END}] Config:')
    print(f'  {Colors.CYAN}*{Colors.END} {Colors.BOLD}repo{Colors.END}={Colors.YELLOW}{config.repo}{Colors.END}')
    print(f'  {Colors.CYAN}*{Colors.END} {Colors.BOLD}commit{Colors.END}={Colors.YELLOW}{config.commit}{Colors.END}')
    print(f'  {Colors.CYAN}*{Colors.END} {Colors.BOLD}repro_per_crash{Colors.END}={Colors.YELLOW}{config.repro_per_crash}{Colors.END}')
    print(f'  {Colors.CYAN}*{Colors.END} {Colors.BOLD}llm_min_per_crash{Colors.END}={Colors.YELLOW}{config.llm_min_per_crash}{Colors.END}')

    if project.has_build_config():
        print(f'[{Colors.GREEN}+{Colors.END}] Build configured')
    else:
        print(f'[{Colors.RED}-{Colors.END}] Build not configured (run {Colors.CYAN}agf gen{Colors.END})')

    harnesses = sorted(project.get_harnesses(), key=lambda x: x.path.name)
    if len(harnesses) == 0:
        print(f'[{Colors.RED}-{Colors.END}] No harnesses found (run {Colors.CYAN}agf infer{Colors.END})')
    else:
        print(f'[{Colors.GREEN}+{Colors.END}] Harnesses:')
        for harness in harnesses:
            print(f'  {Colors.CYAN}*{Colors.END} {harness.path.name} ({Colors.CYAN}{harness.num_crashes()}{Colors.END} crashes, {Colors.CYAN}{harness.num_crash_types()}{Colors.END} types)')

def register(subparsers):
    subparser = subparsers.add_parser('info', help='Display information about the project')
    subparser.add_argument('path', nargs='?', help='The path to the project', default='.')
    subparser.set_defaults(func=do_info)
