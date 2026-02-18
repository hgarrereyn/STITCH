from pathlib import Path
import shutil
import tempfile

from .._colors import ok, fatal
from .._harness import select_harness
from .._project import Project


def do_dev(args):
    path = Path(args.path)
    project = Project(path)
    if not project.exists() or not project.has_config():
        fatal(f"Could not find project at {path.absolute()}")

    project.require_build_config()

    harness = None
    if not args.base:
        harness = select_harness(project, args.name)
        if harness is None:
            return

    image_tag = project.build_docker(args.coverage, args.asan)

    with tempfile.TemporaryDirectory() as tmpdir:
        if args.persist:
            tmpdir = Path(args.path) / "dev"
            tmpdir.mkdir(parents=True, exist_ok=True)
        else:
            tmpdir = Path(tmpdir)
            tmpdir.mkdir(parents=True, exist_ok=True)

        if harness is not None:
            shutil.copy(harness.harness_config(), tmpdir / "harness.json")
        shutil.copy(project.path / "config" / "info.json", tmpdir / "info.json")

        mounts = [(str(tmpdir.absolute()), "/fuzz/workspace")]
        if harness is not None:
            campaign_dir = harness.campaign_dir()
            mounts.append((str(campaign_dir.absolute()), "/fuzz/campaign:ro"))
            if args.coverage:
                mounts.append((str(harness.coverage_dir().absolute()), "/fuzz/coverage"))
        if args.edit:
            stitchi_path = Path(__file__).parent.parent.parent.parent / "stitchi"
            mounts.append((str(stitchi_path.absolute()), "/src/stitchi"))

        env = ["OPENAI_API_KEY"]
        project.invoke(
            env=env,
            mounts=mounts,
            image=image_tag,
            cmd="cd /fuzz/workspace && bash",
        )


def register(subparsers):
    p = subparsers.add_parser("dev", help="Launch an interactive shell inside the Docker container")
    p.add_argument("path", nargs="?", default=".", help="Project directory")
    p.add_argument("-n", "--name", help="Harness name")
    p.add_argument("--base", action="store_true", help="Use the base image (no harness)")
    p.add_argument("--coverage", action="store_true", help="Build with coverage instrumentation")
    p.add_argument("--edit", action="store_true", help="Mount local stitchi source for development")
    p.add_argument("--persist", action="store_true", help="Keep workspace on host after exit")
    p.add_argument("-a", "--asan", action="store_true", help="Enable ASAN")
    p.set_defaults(func=do_dev)
