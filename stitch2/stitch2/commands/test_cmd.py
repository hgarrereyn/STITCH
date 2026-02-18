from pathlib import Path
import shutil
import tempfile

from .._colors import ok, err, fatal


def do_test(args):
    from .._project import Project

    path = Path(args.path)
    project = Project(path)
    if not project.exists() or not project.has_config():
        fatal(f"Could not find project at {path.absolute()}")

    image_tag = project.build_docker()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        tmpdir.mkdir(parents=True, exist_ok=True)
        shutil.copy(project.path / "config" / "info.json", tmpdir / "info.json")
        shutil.copy(project.path / "config" / "test.cpp", tmpdir / "test.cpp")

        mounts = [(str(tmpdir.absolute()), "/fuzz/workspace")]
        try:
            ok("Testing build configuration…")
            project.invoke(
                mounts=mounts,
                image=image_tag,
                cmd="cd /fuzz/workspace && stitchi build external test && ./test",
                check=True,
            )
            ok("Build & run test passed")
        except Exception as e:
            err(f"Test failed: {e}")
            return 1

        try:
            ok("Testing codegen configuration…")
            project.invoke(
                mounts=mounts,
                image=image_tag,
                cmd="cd /fuzz/workspace && stitchi build test-setup",
                check=True,
            )
            ok("Codegen configuration test passed")
        except Exception as e:
            err(f"Codegen test failed: {e}")
            return 1


def register(subparsers):
    p = subparsers.add_parser("test", help="Test the project build configuration")
    p.add_argument("path", nargs="?", default=".", help="Project directory")
    p.set_defaults(func=do_test)
