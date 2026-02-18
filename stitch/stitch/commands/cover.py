from pathlib import Path
import shutil
import tempfile
from typing import Optional

from .._colors import Colors, ok, err, fatal
from .._harness import select_harness


def _get_latest_queue_dir(harness) -> Optional[Path]:
    revisions_dir = harness.path / "campaign" / "revisions"
    if not revisions_dir.exists():
        return None
    revisions = sorted(
        [x for x in revisions_dir.iterdir() if x.is_dir() and x.name.isdigit()],
        key=lambda x: int(x.name),
    )
    if not revisions:
        return None
    queue_dir = revisions[-1] / "outputs" / "queue"
    if not queue_dir.exists():
        return None
    if not any(f.is_file() and not f.name.startswith(".") for f in queue_dir.iterdir()):
        return None
    return queue_dir


def do_cover(args):
    from .._project import Project

    path = Path(args.path)
    project = Project(path)
    if not project.exists() or not project.has_config():
        fatal(f"Could not find project at {path.absolute()}")

    project.require_build_config()

    harness = select_harness(project, args.name)
    if harness is None:
        return

    latest_rev = None
    if args.outputs:
        outputs_dir = Path(args.outputs).absolute()
        queue_dir = outputs_dir / "queue"
        if not queue_dir.exists():
            fatal(f"No queue directory found at {queue_dir}")
        if not any(f.is_file() and not f.name.startswith(".") for f in queue_dir.iterdir()):
            fatal(f"Queue at {queue_dir} is empty")
        ok(f"Using queue from outputs: {queue_dir}")
    else:
        queue_dir = _get_latest_queue_dir(harness)
        if queue_dir is None:
            campaign_dir = harness.campaign_dir()
            if (campaign_dir / "reports").exists() and not (campaign_dir / "revisions").exists():
                fatal(
                    "Minimal-persistence mode — corpus not available on host.\n"
                    f"    {Colors.YELLOW}Tip{Colors.END}: use stitch fuzz --no-triage -o <dir> then stitch cover --outputs <dir>"
                )
            else:
                fatal("No queue found. Run stitch fuzz first.")
        latest_rev = queue_dir.parent.parent
        ok(f"Using queue from revision {latest_rev.name}")

    image_tag = project.build_docker(build_coverage=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        tmpdir.mkdir(parents=True, exist_ok=True)
        shutil.copy(harness.harness_config(), tmpdir / "harness.json")
        shutil.copy(project.path / "config" / "info.json", tmpdir / "info.json")

        coverage_dir = harness.coverage_dir()
        mounts = [
            (str(tmpdir.absolute()), "/fuzz/workspace"),
            (str(coverage_dir.absolute()), "/fuzz/coverage"),
        ]

        if args.outputs:
            mounts.append((str(queue_dir.absolute()), "/fuzz/queue"))
            q_container = "/fuzz/queue"
        else:
            campaign_dir = harness.campaign_dir()
            if not (campaign_dir / "revisions").exists():
                fatal("Campaign revisions not found. Use --outputs instead.")
            mounts.append((str(campaign_dir.absolute()), "/fuzz/campaign"))
            q_container = f"/fuzz/campaign/revisions/{latest_rev.name}/outputs/queue"

        verbose = " --verbose" if args.verbose else ""
        project.invoke(
            mounts=mounts,
            image=image_tag,
            cmd=(
                "cd /fuzz/workspace &&"
                " stitchi zephyr to-schema --meta harness.json --output fuzzer.json &&"
                " stitchi build full fuzzer &&"
                f" stitchi run-cover ./fuzzer {q_container} /fuzz/coverage --nproc {args.nproc}{verbose}"
            ),
        )


def register(subparsers):
    p = subparsers.add_parser("cover", help="Compute code coverage for a harness")
    p.add_argument("path", nargs="?", default=".", help="Project directory")
    p.add_argument("-n", "--name", help="Harness name")
    p.add_argument("--outputs", help="Use queue from a --no-triage outputs directory")
    p.add_argument("--nproc", type=int, default=1, help="Number of processes")
    p.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    p.set_defaults(func=do_cover)
