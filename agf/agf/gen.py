from pathlib import Path
import shutil
import tempfile
import subprocess

from .utils.colors import Colors
from .utils.project import Project
from .utils.env import check_env
from .data.metadata import UsageTracker


def do_gen(args):
    # Speed up imports
    from .gen_agent.agent import run_agent

    path = Path(args.path)
    project = Project(path)
    if not project.exists() or not project.has_config():
        print(f'[{Colors.RED}-{Colors.END}] Could not find project at {path.absolute()}')
        return

    if project.has_build_config():
        if args.force:
            shutil.rmtree(project.path / 'config')
        else:
            print(f'[{Colors.RED}-{Colors.END}] Build configuration already exists. Use --force to overwrite')
            return
        
    config = project.get_config()

    check_env(['OPENAI_API_KEY'])

    print(f'[{Colors.GREEN}+{Colors.END}] Autogenerating build configuration...')

    with tempfile.TemporaryDirectory() as tmpdir:
        src = Path(tmpdir) / 'src'
        if config.commit is not None:
            subprocess.run(['git', 'clone', config.repo, src])
            subprocess.run(['git', 'checkout', config.commit], cwd=src)
        else:
            subprocess.run(['git', 'clone', config.repo, src])

        usage = UsageTracker()
        res = run_agent(config, str(src), usage)

        print(f'[{Colors.GREEN}+{Colors.END}] Usage: {usage.cost_by_task()}')

        if res is None:
            print(f'[{Colors.RED}-{Colors.END}] Autogen failed')
            return
        else:
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
    subparser.set_defaults(func=do_gen)
