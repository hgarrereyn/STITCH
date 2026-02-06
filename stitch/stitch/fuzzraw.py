from pathlib import Path
import os
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
      - selects a harness (like `stitch fuzz`)
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
            if (campaign_dir / 'reports').exists() and not (campaign_dir / 'revisions').exists():
                print(
                    f'[{Colors.RED}-{Colors.END}] Harness revision {args.revision} not available because this campaign '
                    f'is in minimal-persistence mode (no revisions stored on host).\n'
                    f'    {Colors.YELLOW}Tip{Colors.END}: omit --revision to fuzz the base harness.json, or run a workflow that persists revisions.'
                )
            else:
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
    image_tag = project.build_docker(use_asan=asan, hard_rss_limit_mb=args.hard_rss_limit_mb)

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

        # When running in debug mode, enable extra validation and restore the
        # target's stderr so we can see detailed mutation/graph diagnostics.
        debug_cli_flags = ""
        if getattr(args, "debug", False):
            # These environment variables are consumed inside the fuzzer:
            #   - GF_VALIDATE_GRAPHS: enables additional graph validation and
            #     debug printing along the mutation pipeline.
            #   - GF_EXTRA_VERBOSE: turns on extra validation and verbose
            #     mutation/graph logging in the Rust mutation context.
            #   - LIBAFL_FUZZBENCH_DEBUG: re-enables the target's stderr so
            #     that eprintln!/logging output is visible instead of being
            #     fully muted by LibAFL.
            os.environ.setdefault("GF_VALIDATE_GRAPHS", "1")
            os.environ.setdefault("GF_EXTRA_VERBOSE", "1")
            os.environ.setdefault("LIBAFL_FUZZBENCH_DEBUG", "1")
            env.extend(["GF_VALIDATE_GRAPHS", "GF_EXTRA_VERBOSE", "LIBAFL_FUZZBENCH_DEBUG"])
            # `--verbose` turns on additional logging in the Rust pipeline.
            debug_cli_flags = " --verbose"

        # Optional time limit applied to the fuzzer process *inside* the
        # container. This ensures that expensive steps like Docker image
        # builds happen outside the fuzzing time budget, while still
        # enforcing a strict limit on how long the fuzzer itself runs.
        timeout_prefix = ""
        if getattr(args, "time_limit_seconds", None):
            try:
                limit = int(args.time_limit_seconds)
                if limit > 0:
                    # Use coreutils `timeout` so we don't have to assume any
                    # particular fuzzer CLI; this simply bounds wall-clock time
                    # for ./fuzzer. If `timeout` is not available in the image,
                    # this will fail fast with a clear non-zero exit code.
                    timeout_prefix = f"timeout {limit}s "
            except Exception:
                # If parsing fails, just run without an in-container timeout.
                timeout_prefix = ""

        # Roughly mirror what the looper does internally, but without bucketizer/patching:
        #   stitchi zephyr to-schema --meta harness.json --output fuzzer.json
        #   stitchi build full fuzzer
        #   ./fuzzer -i /fuzz/out/inputs -o /fuzz/out --bypass-validation --logfile /fuzz/out/fuzzer.log
        project.invoke(
            env=env,
            mounts=mounts,
            image=image_tag,
            cmd=f'''
                set -e
                cd /fuzz/workspace &&
                stitchi zephyr to-schema --meta harness.json --output fuzzer.json &&
                stitchi build full fuzzer &&
                mkdir -p /fuzz/out/inputs /fuzz/out/queue /fuzz/out/crashes &&
                {timeout_prefix}./fuzzer -i /fuzz/out/inputs -o /fuzz/out --bypass-validation --logfile /fuzz/out/fuzzer.log{debug_cli_flags}
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
        default=0,
    )
    raw.add_argument(
        '--time-limit-seconds',
        type=int,
        help='Optional wall-clock time limit (in seconds) applied to the fuzzer process inside the container.',
        default=None,
    )
    raw.add_argument(
        '-d',
        '--debug',
        action='store_true',
        help='Enable extra validation and verbose mutation/graph logging; keep target stderr',
        default=False,
    )
    raw.set_defaults(func=do_fuzz_raw)


