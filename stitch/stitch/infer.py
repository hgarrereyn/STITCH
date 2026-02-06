from pathlib import Path
import shutil
import tempfile
import sys
import json

from .utils.env import check_env
from .utils.colors import Colors
from .data.project_info import ProjectInfo
from .utils.tee import tee_log


def do_infer(args):
    from .utils.project import Project

    def fail(msg: str, *, cleanup_path: Path = None):
        print(f'[{Colors.RED}-{Colors.END}] {msg}', file=sys.stderr)
        if cleanup_path is not None:
            shutil.rmtree(cleanup_path, ignore_errors=True)
        raise SystemExit(1)

    path = Path(args.path)
    project = Project(path)
    if not project.exists() or not project.has_config():
        fail(f'Could not find project at {path.absolute()}')
    
    harness_name = args.name
    if harness_name is None:
        harness_name = project.next_harness_name()
    harness = project.get_harness(harness_name)
    created_harness = False
    
    if harness.exists():
        if args.force:
            shutil.rmtree(harness.path)
        else:
            fail('Harness already exists. Use --force to overwrite')
    
    harness.create()
    created_harness = True
    
    check_env(['OPENAI_API_KEY'])

    image_tag = project.build_docker()

    info_path = project.path / 'config' / 'info.json'
    if not info_path.exists():
        fail(f'Missing build config file: {info_path}', cleanup_path=harness.path if created_harness else None)
    info = ProjectInfo.model_validate_json(info_path.read_text())

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        tmpdir.mkdir(parents=True, exist_ok=True)

        shutil.copy(project.path / 'config' / 'info.json', tmpdir / 'info.json')

        headers = [x.path for x in info.codegen.headers]
        if not headers:
            fail('Build configuration has 0 headers (info.json codegen.headers is empty)', cleanup_path=harness.path if created_harness else None)

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
        cmd = f'cd /fuzz/workspace && stitchi zephyr run --headers {" ".join(headers)} --output meta.json --threads {args.threads} {"--verbose" if args.verbose else ""}'

        res = project.invoke(
            env=env,
            mounts=mounts,
            image=image_tag,
            cmd=cmd,
        )
        if getattr(res, 'returncode', 1) != 0:
            fail(f'Inference command failed with exit code {res.returncode}', cleanup_path=harness.path if created_harness else None)

        # Save the results
        meta_path = tmpdir / 'meta.json'
        if not meta_path.exists():
            fail(f'Inference did not produce meta.json in {meta_path}', cleanup_path=harness.path if created_harness else None)

        # Sanity check: must have at least one inferred endpoint.
        try:
            meta = json.loads(meta_path.read_text())
        except Exception as e:
            fail(f'Failed to parse meta.json: {e}', cleanup_path=harness.path if created_harness else None)
        endpoints = meta.get('endpoints', []) if isinstance(meta, dict) else []
        if not endpoints:
            fail('Inference generated 0 endpoints (nothing to fuzz)', cleanup_path=harness.path if created_harness else None)

        shutil.copy(meta_path, harness.path / 'harness.json')


def register(subparsers):
    subparser = subparsers.add_parser('infer', help='Infer the schema of the project')
    subparser.add_argument('path', nargs='?', help='The path to the project', default='.')
    subparser.add_argument('--name', help='The name of the harness', default=None)
    subparser.add_argument('--force', action='store_true', help='Force overwrite existing harness')
    subparser.add_argument('--threads', type=int, default=1, help='Number of threads to use for inference')
    subparser.add_argument('--verbose', action='store_true', help='Verbose output')
    subparser.set_defaults(func=do_infer)
