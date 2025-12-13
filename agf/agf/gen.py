from pathlib import Path
import shutil
import tempfile
import subprocess
import sys

from .utils.colors import Colors
from .utils.env import check_env


def do_gen(args):
    def fail(msg: str):
        print(f'[{Colors.RED}-{Colors.END}] {msg}', file=sys.stderr)
        raise SystemExit(1)

    # Import heavy dependencies only after basic argument/project validation so
    # obvious failures don't pay import cost (or trigger optional deps).
    from .utils.project import Project

    path = Path(args.path)
    project = Project(path)
    if not project.exists() or not project.has_config():
        fail(f'Could not find project at {path.absolute()}')

    if project.has_build_config():
        if args.force:
            shutil.rmtree(project.path / 'config')
        else:
            fail('Build configuration already exists. Use --force to overwrite')
        
    config = project.get_config()

    check_env(['OPENAI_API_KEY'])

    # Speed up imports (after validation)
    from .gen_agent.agent import run_agent
    from .data.metadata import UsageTracker

    print(f'[{Colors.GREEN}+{Colors.END}] Autogenerating build configuration...')

    # Build the base image
    print(f'[{Colors.GREEN}+{Colors.END}] Building base image...')
    project.build_base_image('stitch')

    with tempfile.TemporaryDirectory() as tmpdir:
        src = Path(tmpdir) / 'src'
        if config.commit is not None:
            subprocess.run(['git', 'clone', config.repo, src], check=True)
            subprocess.run(['git', 'checkout', config.commit], cwd=src, check=True)
        else:
            subprocess.run(['git', 'clone', config.repo, src], check=True)

        usage = UsageTracker()
        res = run_agent(config, str(src), usage, args.extra)

        print(f'[{Colors.GREEN}+{Colors.END}] Usage: {usage.cost_by_task()}')

        if res is None:
            fail('Autogen failed')
        else:
            # Sanity check: we must have at least one header to generate and fuzz.
            headers = getattr(getattr(getattr(res, 'info', None), 'codegen', None), 'headers', None)
            if not headers:
                fail('Autogen produced a config with 0 headers (codegen.headers is empty)')

            print(f'[{Colors.GREEN}+{Colors.END}] Autogen completed')
    
            # Save the result
            config_path = project.path / 'config'
            config_path.mkdir(parents=True, exist_ok=True)
            (config_path / 'Dockerfile').write_text(res.dockerfile)
            (config_path / 'test.cpp').write_text(res.test_program)
            (config_path / 'info.json').write_text(res.info.model_dump_json(indent=2))
            (config_path / 'usage.json').write_text(usage.model_dump_json(indent=2))


def register(subparsers):
    subparser = subparsers.add_parser('gen', help='Autogenerate a build configuration')
    subparser.add_argument('path', nargs='?', help='The path to the project', default='.')
    subparser.add_argument('-f', '--force', action='store_true', help='Overwrite the build configuration if it already exists')
    subparser.add_argument('-e', '--extra', type=str, help='Extra context to pass to the agent', default=None)
    subparser.set_defaults(func=do_gen)
