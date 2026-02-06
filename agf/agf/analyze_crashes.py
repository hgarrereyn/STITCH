"""
Analyze crashes from a folder without running the fuzzer.

This is a host-side wrapper that builds the Docker image and invokes
`agfi zephyr analyze-crashes` inside the container.
"""

from pathlib import Path
import shutil
import tempfile
from typing import Optional

from .utils.env import check_env
from .utils.colors import Colors
from .utils.project import Project, Harness


def _select_harness(project: Project, name: Optional[str]) -> Optional[Harness]:
    """Shared harness selection logic."""
    harness: Optional[Harness] = None
    if name is None:
        all_harnesses = project.get_harnesses()
        if len(all_harnesses) == 0:
            print(f'[{Colors.RED}-{Colors.END}] No harnesses found')
            return None
        if len(all_harnesses) == 1:
            harness = all_harnesses[0]
        else:
            print(
                f'[{Colors.RED}-{Colors.END}] Multiple harnesses found. '
                f'Please specify one ({Colors.CYAN}--name <name>{Colors.END})'
            )
            for h in all_harnesses:
                print(f'  {Colors.CYAN}*{Colors.END} {h.path.name}')
            return None
    else:
        harness = project.get_harness(name)

    if not harness.exists():
        print(f'[{Colors.RED}-{Colors.END}] Harness {name} does not exist')
        return None

    return harness


def do_analyze_crashes(args):
    """Main entry point for the analyze-crashes command."""
    path = Path(args.path)
    project = Project(path)
    if not project.exists() or not project.has_config():
        print(f'[{Colors.RED}-{Colors.END}] Could not find project at {path.absolute()}')
        return

    harness = _select_harness(project, args.name)
    if harness is None:
        return

    crashes_dir = Path(args.crashes).absolute()
    if not crashes_dir.exists() or not crashes_dir.is_dir():
        print(f'[{Colors.RED}-{Colors.END}] Crashes directory does not exist: {crashes_dir}')
        return

    # Determine output directory
    if args.output:
        output_dir = Path(args.output).absolute()
    else:
        output_dir = harness.analysis_dir().absolute()

    output_dir.mkdir(parents=True, exist_ok=True)

    print(f'[{Colors.GREEN}+{Colors.END}] Project: {project.path.absolute()}')
    print(f'[{Colors.GREEN}+{Colors.END}] Harness: {harness.path.name}')
    print(f'[{Colors.GREEN}+{Colors.END}] Crashes: {crashes_dir}')
    print(f'[{Colors.GREEN}+{Colors.END}] Output: {output_dir}')

    check_env(['OPENAI_API_KEY'])

    # Build the Docker image
    asan = not args.no_asan
    if asan:
        print(f'[{Colors.GREEN}+{Colors.END}] Using ASAN')

    image_tag = project.build_docker(use_asan=asan)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        tmpdir.mkdir(parents=True, exist_ok=True)

        # Copy harness.json and info.json to workspace
        shutil.copy(harness.harness_config(), tmpdir / 'harness.json')
        shutil.copy(project.path / 'config' / 'info.json', tmpdir / 'info.json')

        env = [
            'OPENAI_API_KEY',
        ]

        mounts = [
            (str(tmpdir.absolute()), '/fuzz/workspace'),
            (str(crashes_dir), '/fuzz/crashes'),
            (str(output_dir), '/fuzz/output'),
        ]

        # Run agfi zephyr analyze-crashes inside the container
        project.invoke(
            env=env,
            mounts=mounts,
            image=image_tag,
            cmd='''
                cd /fuzz/workspace &&
                export PYTHONUNBUFFERED=1 &&
                agfi zephyr analyze-crashes --harness harness.json --crashes /fuzz/crashes --output /fuzz/output
            '''
        )

    print(f'[{Colors.GREEN}+{Colors.END}] Analysis complete!')
    print(f'[{Colors.GREEN}+{Colors.END}] Results saved to: {output_dir}')


def register(subparsers):
    """Register the analyze-crashes command."""
    subparser = subparsers.add_parser(
        'analyze-crashes',
        help='Analyze crash files through the distillation pipeline'
    )
    subparser.add_argument('path', nargs='?', help='The path to the project', default='.')
    subparser.add_argument(
        '--crashes',
        type=str,
        required=True,
        help='Path to the folder containing crash files'
    )
    subparser.add_argument('-n', '--name', help='The name of the harness', default=None)
    subparser.add_argument(
        '--output',
        type=str,
        default=None,
        help='Output directory for assessments (default: <harness>/analysis)'
    )
    subparser.add_argument('--no-asan', action='store_true', help='Do not use ASAN', default=False)
    subparser.set_defaults(func=do_analyze_crashes)
