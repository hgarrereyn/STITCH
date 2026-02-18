from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from .._colors import Colors, ok, fatal
from .._env import check_env


def do_gen(args):
    from .._project import Project, resolve_model

    path = Path(args.path)
    project = Project(path)
    if not project.exists() or not project.has_config():
        fatal(f"Could not find project at {path.absolute()}")

    if project.has_build_config():
        if args.force:
            shutil.rmtree(project.path / "config")
        else:
            fatal("Build configuration already exists. Use --force to overwrite")

    config = project.get_config()
    model = resolve_model(getattr(args, "model", None), config)

    check_env(["OPENAI_API_KEY"])

    from ..gen_agent.agent import run_agent
    from ..data.metadata import UsageTracker

    ok(f"Autogenerating build configuration (model: {model})…")
    ok("Building base image…")
    project.build_base_image("stitch")

    with tempfile.TemporaryDirectory() as tmpdir:
        if config.local_path is not None:
            src = Path(config.local_path)
        else:
            src = Path(tmpdir) / "src"
            if config.commit is not None:
                subprocess.run(["git", "clone", config.repo, src], check=True)
                subprocess.run(["git", "checkout", config.commit], cwd=src, check=True)
            else:
                subprocess.run(["git", "clone", config.repo, src], check=True)

        usage = UsageTracker()
        res = run_agent(config, str(src), usage, args.extra, model=model)

        ok(f"Usage: {usage.cost_by_task()}")

        if res is None:
            fatal("Autogen failed")

        headers = getattr(getattr(getattr(res, "info", None), "codegen", None), "headers", None)
        if not headers:
            fatal("Autogen produced a config with 0 headers (codegen.headers is empty)")

        ok("Autogen completed")
        config_path = project.path / "config"
        config_path.mkdir(parents=True, exist_ok=True)
        (config_path / "Dockerfile").write_text(res.dockerfile)
        (config_path / "test.cpp").write_text(res.test_program)
        (config_path / "info.json").write_text(res.info.model_dump_json(indent=2))
        (config_path / "usage.json").write_text(usage.model_dump_json(indent=2))


def register(subparsers):
    p = subparsers.add_parser("gen", help="Auto-generate a build configuration using an LLM agent")
    p.add_argument("path", nargs="?", default=".", help="Project directory")
    p.add_argument("-f", "--force", action="store_true", help="Overwrite existing build config")
    p.add_argument("-e", "--extra", help="Extra context to pass to the agent")
    p.add_argument("-m", "--model", help="LLM model override")
    p.set_defaults(func=do_gen)
