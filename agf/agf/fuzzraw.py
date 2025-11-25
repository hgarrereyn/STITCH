from pathlib import Path
import shutil
import tempfile
from typing import Optional

from .utils.colors import Colors
from .utils.project import Project
from .fuzz import _select_harness


def do_fuzz_raw(args):
    """
    Build the project and run the fuzzer directly (no looper/bucketizer wrapper).

    Intended for evaluation pipelines:
      - selects a harness (like `agf fuzz`)
      - optionally uses a specific harness revision if a campaign exists
      - builds the fuzzer
      - runs it with -i / -o so queue/crash files are written under the chosen output dir
    """
    path = Path(args.path)
    project = Project(path)
    if not project.exists() or not project.has_config():
        print(f'[{Colors.RED}-{Colors.END}] Could not find project at {path.absolute()}')
        return

    harness = _select_harness(project, args.name)
    if harness is None:
        return

    # Determine which harness.json to use: base harness or a specific revision.
    if args.revision is None:
        harness_json = harness.harness_config()
        if not harness_json.exists():
            print(f'[{Colors.RED}-{Colors.END}] Harness config not found at {harness_json}')
            return
    else:
        campaign_dir = harness.campaign_dir()
        rev_dir = campaign_dir / 'revisions' / str(args.revision)
        harness_json = rev_dir / 'harness.json'
        if not rev_dir.exists() or not harness_json.exists():
            print(
                f'[{Colors.RED}-{Colors.END}] Harness revision {args.revision} not found '
                f'for harness {harness.path.name}'
            )
            return

    out_dir = Path(args.output_dir).absolute()
    out_dir.mkdir(parents=True, exist_ok=True)

    asan = not args.no_asan
    if asan:
        print(f'[{Colors.GREEN}+{Colors.END}] Using ASAN')

    # For raw fuzzing we don't need OPENAI access; we're using an existing harness.
    project.build_docker(use_asan=asan, hard_rss_limit_mb=args.hard_rss_limit_mb)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        tmpdir.mkdir(parents=True, exist_ok=True)

        # Workspace in the container: harness + info.json + fuzzer artifacts
        shutil.copy(harness_json, tmpdir / 'harness.json')
        shutil.copy(project.path / 'config' / 'info.json', tmpdir / 'info.json')

        mounts = [
            (str(tmpdir.absolute()), '/fuzz/workspace'),
            (str(out_dir.absolute()), '/fuzz/out'),
        ]

        env = []

        # Roughly mirror what the looper does internally, but without bucketizer/patching:
        #   agfi zephyr to-schema --meta harness.json --output fuzzer.json
        #   agfi build full fuzzer
        #   ./fuzzer -i /fuzz/out/inputs -o /fuzz/out --bypass-validation --logfile /fuzz/out/fuzzer.log --rss-limit-mb 0
        project.invoke(
            env=env,
            mounts=mounts,
            cmd='''
                set -e
                cd /fuzz/workspace &&
                agfi zephyr to-schema --meta harness.json --output fuzzer.json &&
                agfi build full fuzzer &&
                mkdir -p /fuzz/out/inputs /fuzz/out/queue /fuzz/out/crashes &&
                ./fuzzer -i /fuzz/out/inputs -o /fuzz/out --bypass-validation --logfile /fuzz/out/fuzzer.log --rss-limit-mb 0
            ''',
        )


def register(subparsers):
    # Raw fuzz command: just build and run the fuzzer binary, no looper/bucketizer/patching.
    raw = subparsers.add_parser(
        'fuzz-raw',
        help='Build the project and run the fuzzer directly (no wrapper); useful for eval pipelines',
    )
    raw.add_argument('path', nargs='?', help='The path to the project', default='.')
    raw.add_argument('-n', '--name', help='The name of the harness', default=None)
    raw.add_argument(
        '--revision',
        type=int,
        help='Harness revision to run (from the campaign directory); defaults to the base harness.json',
        default=None,
    )
    raw.add_argument(
        '-o',
        '--output-dir',
        required=True,
        help='Host directory where the fuzzer will write queue/crash files',
    )
    raw.add_argument('--no-asan', action='store_true', help='Do not use ASAN', default=False)
    raw.add_argument(
        '--hard-rss-limit-mb',
        type=int,
        help='The hard RSS limit in MB (propagated via ASAN_OPTIONS in the Docker image)',
        default=1024,
    )
    raw.set_defaults(func=do_fuzz_raw)


