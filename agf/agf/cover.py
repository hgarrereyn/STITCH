from pathlib import Path
from typing import Optional
import shutil
import tempfile

from .utils.colors import Colors
from .utils.project import Project, Harness


def _get_latest_queue_dir(harness: Harness) -> Optional[Path]:
    """
    Locate the latest queue directory for a harness based on the campaign layout:
      harness/campaign/revisions/<rev>/outputs/queue
    """
    campaign_dir = harness.path / 'campaign'
    revisions_dir = campaign_dir / 'revisions'

    if not revisions_dir.exists():
        return None

    revisions = sorted(
        [x for x in revisions_dir.iterdir() if x.is_dir() and x.name.isdigit()],
        key=lambda x: int(x.name),
    )
    if not revisions:
        return None

    latest_rev = revisions[-1]
    queue_dir = latest_rev / 'outputs' / 'queue'
    if not queue_dir.exists():
        return None

    has_files = any(
        f.is_file() and not f.name.startswith('.') for f in queue_dir.iterdir()
    )
    if not has_files:
        return None

    return queue_dir


def do_cover(args):
    path = Path(args.path)
    project = Project(path)
    if not project.exists() or not project.has_config():
        print(f'[{Colors.RED}-{Colors.END}] Could not find project at {path.absolute()}')
        return

    harness: Optional[Harness] = None
    if args.name is None:
        all_harnesses = project.get_harnesses()
        if len(all_harnesses) == 0:
            print(f'[{Colors.RED}-{Colors.END}] No harnesses found')
            return
        if len(all_harnesses) == 1:
            harness = all_harnesses[0]
        else:
            print(f'[{Colors.RED}-{Colors.END}] Multiple harnesses found. Please specify one ({Colors.CYAN}--name <name>{Colors.END})')
            for h in all_harnesses:
                print(f'  {Colors.CYAN}*{Colors.END} {h.path.name}')
            return
    else:
        harness = project.get_harness(args.name)

    if harness is None or not harness.exists():
        print(f'[{Colors.RED}-{Colors.END}] Harness {args.name} does not exist')
        return

    # Decide which queue to use:
    #  - default: latest queue from the fuzz campaign (agf fuzz)
    #  - if --outputs is provided: queue from a fuzz-raw outputs directory
    latest_rev = None
    if getattr(args, 'outputs', None):
        outputs_dir = Path(args.outputs).absolute()
        queue_dir = outputs_dir / 'queue'
        if not queue_dir.exists():
            print(f'[{Colors.RED}-{Colors.END}] No queue directory found at {queue_dir}')
            return
        has_files = any(
            f.is_file() and not f.name.startswith('.') for f in queue_dir.iterdir()
        )
        if not has_files:
            print(f'[{Colors.RED}-{Colors.END}] Queue at {queue_dir} is empty')
            return
        print(f'[{Colors.GREEN}+{Colors.END}] Using queue from fuzz-raw outputs: {queue_dir}')
    else:
        # Find latest queue from the campaign
        queue_dir = _get_latest_queue_dir(harness)
        if queue_dir is None:
            campaign_dir = harness.campaign_dir()
            # Minimal-persistence mode writes only reports/buckets/seeds to the host campaign dir,
            # so revisions/corpus are not available for offline coverage.
            if (campaign_dir / 'reports').exists() and not (campaign_dir / 'revisions').exists():
                print(
                    f'[{Colors.RED}-{Colors.END}] This harness campaign is in minimal-persistence mode '
                    f'(no {campaign_dir / "revisions"} on host), so the corpus queue is not available for `agf cover`.\n'
                    f'    {Colors.YELLOW}Tip{Colors.END}: run `agf fuzz-raw -o <outdir>` and then `agf cover --outputs <outdir>`.'
                )
            else:
                print(
                    f'[{Colors.RED}-{Colors.END}] No queue found for harness {harness.path.name}. '
                    f'Make sure you have run `agf fuzz` first.'
                )
            return

        latest_rev = queue_dir.parent.parent  # .../revisions/<rev>/outputs/queue -> .../revisions/<rev>
        print(f'[{Colors.GREEN}+{Colors.END}] Using queue from revision {latest_rev.name}')

    # Build coverage-instrumented image
    image_tag = project.build_docker(build_coverage=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        tmpdir.mkdir(parents=True, exist_ok=True)
        shutil.copy(harness.harness_config(), tmpdir / 'harness.json')
        shutil.copy(project.path / 'config' / 'info.json', tmpdir / 'info.json')

        coverage_dir = harness.coverage_dir()

        # Mounts differ depending on where the queue comes from
        mounts = [
            (str(tmpdir.absolute()), '/fuzz/workspace'),
            (str(coverage_dir.absolute()), '/fuzz/coverage'),
        ]

        if getattr(args, 'outputs', None):
            # Queue from fuzz-raw outputs
            mounts.append((str(queue_dir.absolute()), '/fuzz/queue'))
            queue_dir_container = '/fuzz/queue'
        else:
            # Queue from the campaign revisions
            campaign_dir = harness.campaign_dir()
            if not (campaign_dir / 'revisions').exists():
                print(
                    f'[{Colors.RED}-{Colors.END}] Campaign revisions not found at {campaign_dir / "revisions"}. '
                    f'This is expected in minimal-persistence mode; use --outputs instead.'
                )
                return
            mounts.append((str(campaign_dir.absolute()), '/fuzz/campaign'))
            queue_dir_container = f'/fuzz/campaign/revisions/{latest_rev.name}/outputs/queue'

        project.invoke(
            mounts=mounts,
            image=image_tag,
            cmd=f'''
                cd /fuzz/workspace &&
                agfi zephyr to-schema --meta harness.json --output fuzzer.json &&
                agfi build full fuzzer &&
                agfi run-cover ./fuzzer {queue_dir_container} /fuzz/coverage --nproc {args.nproc} {f'--verbose' if args.verbose else ''}
            ''',
        )


def register(subparsers):
    subparser = subparsers.add_parser('cover', help='Compute coverage for a harness')
    subparser.add_argument('path', nargs='?', help='The path to the project', default='.')
    subparser.add_argument('--name', help='The name of the harness', default=None)
    subparser.add_argument(
        '--outputs',
        type=str,
        default=None,
        help='Use queue from a fuzz-raw outputs directory (overrides campaign-based queue lookup)',
    )
    subparser.add_argument('--nproc', type=int, default=1, help='Number of processes to use')
    subparser.add_argument('--verbose', action='store_true', help='Verbose output')
    subparser.set_defaults(func=do_cover)
