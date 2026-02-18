"""Unified fuzz command.

``stitch fuzz``           – full fuzzing loop with triage & patching
``stitch fuzz --no-triage`` – raw fuzzer execution (no looper/bucketizer)
"""

from pathlib import Path
import os
import shutil
import signal
import subprocess
import tempfile
import time
from typing import Optional

from .._colors import Colors, ok, err, warn, fatal
from .._env import check_env
from .._harness import select_harness
from .._project import Project, resolve_model


# ── Raw mode (replaces old ``fuzz-raw``) ─────────────────────────────


_INFER_HINT = f"Run {Colors.BOLD}stitch infer{Colors.END} first to generate a harness."


def _do_fuzz_raw(args, project: Project):
    harness = select_harness(project, args.name, hint=_INFER_HINT)
    if harness is None:
        return

    if not args.output_dir:
        fatal("--output-dir / -o is required in --no-triage mode")

    if args.revision is None:
        harness_json = harness.harness_config()
        if not harness_json.exists():
            fatal(f"Harness config not found at {harness_json}")
    else:
        campaign_dir = harness.campaign_dir()
        rev_dir = campaign_dir / "revisions" / str(args.revision)
        harness_json = rev_dir / "harness.json"
        if not rev_dir.exists() or not harness_json.exists():
            if (campaign_dir / "reports").exists() and not (campaign_dir / "revisions").exists():
                fatal(
                    f"Harness revision {args.revision} not available (minimal-persistence mode). "
                    f"Omit --revision to fuzz the base harness.json."
                )
            else:
                fatal(f"Harness revision {args.revision} not found for harness {harness.path.name}")

    out_dir = Path(args.output_dir).absolute()
    out_dir.mkdir(parents=True, exist_ok=True)

    asan = not args.no_asan
    if asan:
        ok("Using ASAN")

    image_tag = project.build_docker(use_asan=asan, hard_rss_limit_mb=args.hard_rss_limit_mb)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        tmpdir.mkdir(parents=True, exist_ok=True)
        shutil.copy(harness_json, tmpdir / "harness.json")
        shutil.copy(project.path / "config" / "info.json", tmpdir / "info.json")

        mounts = [
            (str(tmpdir.absolute()), "/fuzz/workspace"),
            (str(out_dir.absolute()), "/fuzz/out"),
        ]
        env: list[str] = []

        debug_cli_flags = ""
        if getattr(args, "debug", False):
            os.environ.setdefault("GF_VALIDATE_GRAPHS", "1")
            os.environ.setdefault("GF_EXTRA_VERBOSE", "1")
            os.environ.setdefault("LIBAFL_FUZZBENCH_DEBUG", "1")
            env.extend(["GF_VALIDATE_GRAPHS", "GF_EXTRA_VERBOSE", "LIBAFL_FUZZBENCH_DEBUG"])
            debug_cli_flags = " --verbose"

        timeout_prefix = ""
        limit = getattr(args, "time_limit_seconds", None)
        if limit is not None and int(limit) > 0:
            timeout_prefix = f"timeout {int(limit)}s "

        project.invoke(
            env=env,
            mounts=mounts,
            image=image_tag,
            cmd=(
                "set -e && cd /fuzz/workspace &&"
                " stitchi zephyr to-schema --meta harness.json --output fuzzer.json &&"
                " stitchi build full fuzzer &&"
                " mkdir -p /fuzz/out/inputs /fuzz/out/queue /fuzz/out/crashes &&"
                f" {timeout_prefix}./fuzzer -i /fuzz/out/inputs -o /fuzz/out"
                f" --bypass-validation --logfile /fuzz/out/fuzzer.log{debug_cli_flags}"
            ),
        )


# ── Full mode (looper + triage + patching) ───────────────────────────


def _do_fuzz_full(args, project: Project):
    model = resolve_model(getattr(args, "model", None), project.get_config())
    ok(f"Using model: {model}")

    harness = select_harness(project, args.name, hint=_INFER_HINT)
    if harness is None:
        return

    asan = not args.no_asan
    if asan:
        ok("Using ASAN")

    check_env(["OPENAI_API_KEY"])

    image_tag = project.build_docker(use_asan=asan, hard_rss_limit_mb=args.hard_rss_limit_mb)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        tmpdir.mkdir(parents=True, exist_ok=True)
        shutil.copy(harness.harness_config(), tmpdir / "harness.json")
        shutil.copy(project.path / "config" / "info.json", tmpdir / "info.json")

        host_campaign = harness.campaign_dir().absolute()
        reports_dir = host_campaign / "reports"
        buckets_dir = host_campaign / "buckets"
        seeds_dir = host_campaign / "seeds"
        revisions_dir = host_campaign / "revisions"
        control_dir = host_campaign / "control"
        for d in (reports_dir, buckets_dir, seeds_dir, revisions_dir, control_dir):
            d.mkdir(parents=True, exist_ok=True)

        stop_file = control_dir / "stop"

        mounts = [
            (str(tmpdir.absolute()), "/fuzz/workspace"),
            (str(reports_dir), "/fuzz/persist/reports"),
            (str(buckets_dir), "/fuzz/persist/buckets"),
            (str(seeds_dir), "/fuzz/persist/seeds"),
            (str(revisions_dir), "/fuzz/persist/revisions"),
            (str(control_dir), "/fuzz/persist/control"),
        ]

        os.environ["STITCH_MODEL"] = model
        env = ["OPENAI_API_KEY", "STITCH_MODEL"]

        reset_flag = " --reset" if args.reset else ""
        corpus_flag = " --no-corpus-copy" if args.no_corpus_copy else ""

        sigint_count = 0
        force_exit = False

        def _on_sigint(_signum, _frame):
            nonlocal sigint_count, force_exit
            sigint_count += 1
            if sigint_count == 1:
                try:
                    stop_file.parent.mkdir(parents=True, exist_ok=True)
                    stop_file.touch(exist_ok=True)
                except Exception:
                    pass
                warn("Graceful stop requested. Press Ctrl+C again to force quit.")
            else:
                force_exit = True
                err("Force quitting (second Ctrl+C)…")

        old_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, _on_sigint)

        p = project.invoke(
            env=env,
            mounts=mounts,
            image=image_tag,
            cmd=(
                "cd /fuzz/workspace &&"
                " mkdir -p /fuzz/persist/reports /fuzz/persist/buckets /fuzz/persist/seeds /fuzz/persist/revisions &&"
                " export PYTHONUNBUFFERED=1 &&"
                " ("
                "   command -v stdbuf >/dev/null 2>&1 &&"
                f"  exec stdbuf -oL -eL stitchi zephyr looper --harness harness.json --campaign /tmp/stitch_campaign --persist /fuzz/persist --stop-file /fuzz/persist/control/stop --max-revisions {args.max_revisions}{reset_flag}{corpus_flag}"
                " ) || ("
                f"  exec stitchi zephyr looper --harness harness.json --campaign /tmp/stitch_campaign --persist /fuzz/persist --stop-file /fuzz/persist/control/stop --max-revisions {args.max_revisions}{reset_flag}{corpus_flag}"
                " )"
            ),
            popen=True,
            use_tty=False,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
        )

        try:
            while True:
                rc = p.poll()
                if rc is not None:
                    return rc
                if force_exit:
                    try:
                        os.killpg(p.pid, signal.SIGTERM)
                    except Exception:
                        pass
                    deadline = time.time() + 2.0
                    while time.time() < deadline and p.poll() is None:
                        time.sleep(0.05)
                    if p.poll() is None:
                        try:
                            os.killpg(p.pid, signal.SIGKILL)
                        except Exception:
                            pass
                    try:
                        p.wait(timeout=5.0)
                    except Exception:
                        pass
                    return
                time.sleep(0.2)
        finally:
            signal.signal(signal.SIGINT, old_handler)


# ── Dispatcher ───────────────────────────────────────────────────────


def do_fuzz(args):
    path = Path(args.path)
    project = Project(path)
    if not project.exists() or not project.has_config():
        fatal(f"Could not find project at {path.absolute()}")

    project.require_build_config()

    if args.no_triage:
        return _do_fuzz_raw(args, project)
    return _do_fuzz_full(args, project)


def register(subparsers):
    p = subparsers.add_parser(
        "fuzz",
        help="Run the fuzzer (with automatic triage by default)",
        formatter_class=lambda prog: __import__("argparse").RawDescriptionHelpFormatter(prog, max_help_position=35),
        epilog=(
            "examples:\n"
            "  stitch fuzz                           # full loop with triage & patching\n"
            "  stitch fuzz --no-triage -o out/        # raw fuzzer, no triage\n"
            "  stitch fuzz --no-triage -o out/ -d     # raw fuzzer with debug logging\n"
        ),
    )

    # ── Common flags ──
    p.add_argument("path", nargs="?", default=".", help="Project directory")
    p.add_argument("-n", "--name", help="Harness name")
    p.add_argument("--no-asan", action="store_true", help="Disable ASAN")
    p.add_argument("--hard-rss-limit-mb", type=int, default=1024, help="Hard RSS limit (MB)")
    p.add_argument("-m", "--model", help="LLM model override")

    # ── Full-mode flags ──
    p.add_argument("--max-revisions", type=int, default=30, help="Max revision iterations")
    p.add_argument("--reset", action="store_true", help="Reset the campaign before starting")
    p.add_argument("--no-corpus-copy", action="store_true", help="Don't copy corpus between revisions")

    # ── Raw / no-triage mode ──
    p.add_argument("--no-triage", action="store_true", help="Raw fuzzer only (no looper/bucketizer/patching)")
    p.add_argument("-o", "--output-dir", help="Host output directory (required with --no-triage)")
    p.add_argument("--revision", type=int, help="Use a specific harness revision (--no-triage only)")
    p.add_argument("--time-limit-seconds", type=int, help="Wall-clock time limit for the fuzzer (--no-triage only)")
    p.add_argument("-d", "--debug", action="store_true", help="Enable verbose debug logging (--no-triage only)")

    p.set_defaults(func=do_fuzz)
