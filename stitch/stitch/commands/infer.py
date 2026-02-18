from pathlib import Path
import json
import os
import shutil
import sys
import tempfile

from .._colors import Colors, ok, fatal
from .._env import check_env


def do_infer(args):
    from .._project import Project, resolve_model
    from ..data.project_info import ProjectInfo

    path = Path(args.path)
    project = Project(path)
    if not project.exists() or not project.has_config():
        fatal(f"Could not find project at {path.absolute()}")

    project.require_build_config()

    config = project.get_config()
    model = resolve_model(getattr(args, "model", None), config)
    ok(f"Using model: {model}")

    harness_name = args.name or project.next_harness_name()
    harness = project.get_harness(harness_name)
    created_harness = False

    if harness.exists():
        if args.force:
            shutil.rmtree(harness.path)
        else:
            fatal("Harness already exists. Use --force to overwrite")

    harness.create()
    created_harness = True

    def _cleanup_fatal(msg: str):
        if created_harness:
            shutil.rmtree(harness.path, ignore_errors=True)
        fatal(msg)

    check_env(["OPENAI_API_KEY"])

    image_tag = project.build_docker()

    info_path = project.path / "config" / "info.json"
    if not info_path.exists():
        _cleanup_fatal(f"Missing build config file: {info_path}")
    info = ProjectInfo.model_validate_json(info_path.read_text())

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        tmpdir.mkdir(parents=True, exist_ok=True)
        shutil.copy(info_path, tmpdir / "info.json")

        headers = [x.path for x in info.codegen.headers]
        if not headers:
            _cleanup_fatal("Build configuration has 0 headers")

        os.environ["STITCH_MODEL"] = model
        env = [
            "OPENAI_API_KEY",
            "LANGSMITH_API_KEY",
            "LANGSMITH_PROJECT",
            "LANGSMITH_ENDPOINT",
            "LANGSMITH_TRACING",
            "STITCH_MODEL",
        ]
        mounts = [(str(tmpdir), "/fuzz/workspace")]
        header_args = " ".join(headers)
        cmd = (
            f"cd /fuzz/workspace && stitchi zephyr run"
            f" --headers {header_args}"
            f" --output meta.json"
            f" --threads {args.threads}"
            f'{" --verbose" if args.verbose else ""}'
        )

        res = project.invoke(env=env, mounts=mounts, image=image_tag, cmd=cmd)
        if getattr(res, "returncode", 1) != 0:
            _cleanup_fatal(f"Inference failed with exit code {res.returncode}")

        meta_path = tmpdir / "meta.json"
        if not meta_path.exists():
            _cleanup_fatal("Inference did not produce meta.json")

        try:
            meta = json.loads(meta_path.read_text())
        except Exception as e:
            _cleanup_fatal(f"Failed to parse meta.json: {e}")

        endpoints = meta.get("endpoints", []) if isinstance(meta, dict) else []
        if not endpoints:
            _cleanup_fatal("Inference generated 0 endpoints (nothing to fuzz)")

        shutil.copy(meta_path, harness.path / "harness.json")
        ok(f"Harness {Colors.BOLD}{harness_name}{Colors.END} created with {len(endpoints)} endpoints")


def register(subparsers):
    p = subparsers.add_parser("infer", help="Infer a fuzz harness specification for the project")
    p.add_argument("path", nargs="?", default=".", help="Project directory")
    p.add_argument("-n", "--name", help="Harness name (auto-generated if omitted)")
    p.add_argument("-f", "--force", action="store_true", help="Overwrite existing harness")
    p.add_argument("--threads", type=int, default=1, help="Parallel inference threads")
    p.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    p.add_argument("-m", "--model", help="LLM model override")
    p.set_defaults(func=do_infer)
