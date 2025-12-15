from pathlib import Path
import json
import shutil
import tempfile

from .utils.colors import Colors
from .utils.project import Project


def do_bug(args):
    path = Path(args.path)
    project = Project(path)
    if not project.exists() or not project.has_config():
        print(f'[{Colors.RED}-{Colors.END}] Could not find project at {path.absolute()}')
        return

    asan = not args.no_asan
    if asan:
        print(f'[{Colors.GREEN}+{Colors.END}] Using ASAN')

    image_tag = project.build_docker(use_asan=asan, override_commit=args.commit)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        tmpdir.mkdir(parents=True, exist_ok=True)
        shutil.copy(project.path / 'config' / 'info.json', tmpdir / 'info.json')
        shutil.copy(args.testcase, tmpdir / 'test.cpp')

        mounts = [
            (str(tmpdir.absolute()), '/fuzz/workspace'),
        ]

        # When an output path is provided (and we're not in debug mode), wrap
        # the `./test` invocation so that its stdout/stderr are *individually*
        # captured to files inside the container while still streaming through
        # to the terminal. This lets us record exactly what the binary printed
        # (including ANSI escapes) rather than capturing Docker's aggregate
        # stdout/stderr.
        stdout_log = tmpdir / 'bug_stdout.log'
        stderr_log = tmpdir / 'bug_stderr.log'
        casr_san_log = tmpdir / 'casr_san.json'
        casr_gdb_log = tmpdir / 'casr_gdb.json'

        if args.debug:
            cmd = f'''
                cd /fuzz/workspace &&
                agfi build external test &&
                pwndbg ./test
            '''
        else:
            if args.output is not None:
                # Run the test and capture output, then also run casr-san and casr-gdb
                # to gather detailed crash analysis if a crash occurs
                cmd = r'''
                    cd /fuzz/workspace &&
                    agfi build external test &&
                    ./test > >(tee bug_stdout.log) 2> >(tee bug_stderr.log >&2)
                    TEST_EXIT=$?
                    if [ $TEST_EXIT -ne 0 ]; then
                        # Run casr-san to analyze sanitizer output
                        timeout 10 casr-san --output casr_san.json -- ./test 2>/dev/null || true
                        # Run casr-gdb for additional crash analysis
                        timeout 10 casr-gdb --output casr_gdb.json -- ./test 2>/dev/null || true
                    fi
                    exit $TEST_EXIT
                '''
            else:
                cmd = '''
                    cd /fuzz/workspace &&
                    agfi build external test &&
                    ./test
                '''

        result = project.invoke(
            mounts=mounts,
            image=image_tag,
            cmd=cmd,
            check=False,
        )

        # If an output path was requested (in non-debug mode), read back the
        # captured streams from the workspace and emit them as JSON.
        if args.output is not None and not args.debug:
            stdout = stdout_log.read_text(errors='replace') if stdout_log.exists() else ''
            stderr = stderr_log.read_text(errors='replace') if stderr_log.exists() else ''

            # Try to load CASR analysis results
            casr_san = None
            casr_gdb = None
            try:
                if casr_san_log.exists():
                    casr_san = json.loads(casr_san_log.read_text(errors='replace'))
            except Exception:
                pass
            try:
                if casr_gdb_log.exists():
                    casr_gdb = json.loads(casr_gdb_log.read_text(errors='replace'))
            except Exception:
                pass

            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(
                    {
                        'stdout': stdout,
                        'stderr': stderr,
                        'returncode': result.returncode,
                        'casr_san': casr_san,
                        'casr_gdb': casr_gdb,
                    },
                    indent=2,
                )
            )


def register(subparsers):
    subparser = subparsers.add_parser('bug', help='Compile and run a provided testcase')
    subparser.add_argument('path', nargs='?', help='The path to the project', default='.')
    subparser.add_argument('-t', '--testcase', type=str, help='The path to the testcase (.cpp)', default=None)
    subparser.add_argument('-c', '--commit', type=str, help='Test on a specific commit', default=None)
    subparser.add_argument('--no-asan', action='store_true', help='Do not use ASAN', default=False)
    subparser.add_argument('-d', '--debug', action='store_true', help='Spawn a shell', default=False)
    subparser.add_argument('-r', '--report', action='store_true', help='Generate a markdown report', default=False)
    subparser.add_argument(
        '-o',
        '--output',
        type=str,
        help='Write JSON with stdout/stderr from the run to this path',
        default=None,
    )
    subparser.set_defaults(func=do_bug)
