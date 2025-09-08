from pathlib import Path
import shutil
import tempfile

from .utils.env import check_env
from .utils.colors import Colors
from .utils.project import Project
from .data.project_info import ProjectInfo
from .utils.tee import tee_log


def do_infer(args):
    path = Path(args.path)
    project = Project(path)
    if not project.exists() or not project.has_config():
        print(f'[{Colors.RED}-{Colors.END}] Could not find project at {path.absolute()}')
        return
    
    harness_name = args.name
    if harness_name is None:
        harness_name = project.next_harness_name()
    harness = project.get_harness(harness_name)
    
    if harness.exists():
        if args.force:
            shutil.rmtree(harness.path)
        else:
            print(f'[{Colors.RED}-{Colors.END}] Harness already exists. Use --force to overwrite')
            return
    
    harness.create()
    
    check_env(['OPENAI_API_KEY'])

    project.build_docker()

    info = ProjectInfo.model_validate_json(open(project.path / 'config' / 'info.json').read())

    # infer_log = harness.path / 'infer.log'
    # infer_log.touch()

    # tee_log(str(infer_log))

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        tmpdir.mkdir(parents=True, exist_ok=True)

        shutil.copy(project.path / 'config' / 'info.json', tmpdir / 'info.json')

        headers = [x.name for x in info.codegen.headers]

        env = [
            'OPENAI_API_KEY',
            'LANGSMITH_API_KEY',
            'LANGSMITH_PROJECT',
            'LANGSMITH_ENDPOINT',
            'LANGSMITH_TRACING',
        ]
        mounts = [
            (str(tmpdir), '/fuzz/workspace')
        ]
        # project.invoke(env=env, mounts=mounts, cmd=f'cd /fuzz/workspace && agfi flow full --headers {" ".join(headers)} --model {args.model} --output meta.json')
        # project.invoke(env=env, mounts=mounts, cmd=f'cd /fuzz/workspace && agfi flow gen-stubs --meta /fuzz/workspace/meta.json --model {args.model} --output /fuzz/workspace/meta2.json')
        # project.invoke(env=env, mounts=mounts, cmd=f'cd /fuzz/workspace && agfi flow gen-templates --meta /fuzz/workspace/meta2.json --model {args.model} --output /fuzz/workspace/meta3.json')
        # project.invoke(env=env, mounts=mounts, cmd=f'cd /fuzz/workspace && agfi flow gen-hints --meta /fuzz/workspace/meta3.json --model {args.model} --output /fuzz/workspace/meta4.json')

        cmd = f'cd /fuzz/workspace && agfi inc run --headers {" ".join(headers)} --model {args.model} --output meta.json --threads {args.threads}'
        if args.strip:
            cmd += ' --strip'

        project.invoke(env=env, mounts=mounts, cmd=cmd)

        # Save the results
        shutil.copy(tmpdir / 'meta.json', harness.path / 'harness.json')


def register(subparsers):
    subparser = subparsers.add_parser('infer', help='Infer the schema of the project')
    subparser.add_argument('path', nargs='?', help='The path to the project', default='.')
    subparser.add_argument('--name', help='The name of the harness', default=None)
    subparser.add_argument('--force', action='store_true', help='Force overwrite existing harness')
    subparser.add_argument('--model', type=str, default='gpt-4.1', help='Model to use for inference')
    subparser.add_argument('--threads', type=int, default=1, help='Number of threads to use for inference')
    subparser.add_argument('--strip', action='store_true', help='Strip extra metadata from the output file')
    subparser.set_defaults(func=do_infer)
