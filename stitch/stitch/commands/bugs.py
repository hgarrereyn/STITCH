"""Bug management subcommand group.

Consolidates: multi, replay, save, triage, analyze-crashes, report, run/bug, exec.

Usage:
    stitch bugs list          List bug reports across projects
    stitch bugs replay        Replay crashes from a campaign
    stitch bugs save          Save a specific bug to disk
    stitch bugs triage        Run crash triage pipeline
    stitch bugs analyze       Analyze raw crash files
    stitch bugs report        Generate an external bug report
    stitch bugs run           Compile and run a testcase
    stitch bugs exec          Execute a raw graph testcase
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .._colors import Colors, ok, err, warn, fatal
from .._env import check_env
from .._harness import select_harness


# ═══════════════════════════════════════════════════════════════════════
# bugs list  (was: multi)
# ═══════════════════════════════════════════════════════════════════════


class _BugReport:
    def __init__(self, report_dir: Path):
        self.report_dir = report_dir
        self._info = None
        self._assessment = None
        self.status: Optional[str] = None

    @property
    def info(self):
        if self._info is None and (self.report_dir / "info.json").exists():
            try:
                self._info = json.loads((self.report_dir / "info.json").read_text())
            except Exception:
                pass
        return self._info

    @property
    def assessment(self):
        if self._assessment is None and (self.report_dir / "assessment.json").exists():
            try:
                self._assessment = json.loads((self.report_dir / "assessment.json").read_text())
            except Exception:
                pass
        return self._assessment

    @property
    def severity(self) -> str:
        return (self.assessment or {}).get("crash_type", "unknown")

    @property
    def title(self) -> str:
        return (self.assessment or {}).get("title", "Untitled")

    @property
    def bucket_hash(self) -> str:
        return (self.info or {}).get("bucket_hash", "unknown")

    def matches_severity(self, filt: Optional[str]) -> bool:
        if filt is None:
            return True
        s = self.severity.lower()
        f = filt.lower()
        if f in ("crit", "critical"):
            return "crit" in s
        if f == "high":
            return "high" in s
        if f in ("med", "medium"):
            return "med" in s
        if f == "low":
            return "low" in s
        if f == "invalid":
            return s.startswith("invalid")
        if f == "valid":
            return s.startswith("valid")
        return True


def _severity_color(severity: str) -> str:
    s = severity.lower()
    if s.startswith("invalid"):
        return Colors.RED
    if "-crit" in s:
        return Colors.LIGHT_RED
    if "-high" in s:
        return Colors.YELLOW
    if "-med" in s:
        return Colors.CYAN
    if "-low" in s:
        return Colors.GREEN
    if s.startswith("valid"):
        return Colors.LIGHT_CYAN
    return Colors.CYAN


def do_list(args):
    from .._project import Project

    root = Path(args.path)
    if not root.is_dir():
        fatal(f"Path {root.absolute()} is not a directory")

    projects = []
    for sd in root.iterdir():
        if sd.is_dir() and (sd / "stitch.json").exists():
            try:
                p = Project(sd)
                if p.has_config():
                    projects.append((sd, p))
            except Exception:
                pass

    if not projects:
        warn(f"No projects found in {root.absolute()}")
        return

    ok(f"Found {len(projects)} projects")

    all_reports: Dict[str, Dict[str, List[_BugReport]]] = defaultdict(lambda: defaultdict(list))

    for project_path, project in projects:
        pname = project_path.name
        status_data = {}
        sf = project.status_file()
        if sf.exists():
            try:
                status_data = json.loads(sf.read_text())
            except Exception:
                pass

        for harness in project.get_harnesses():
            reports_dir = harness.campaign_dir() / "reports"
            if not reports_dir.exists():
                continue
            for rd in reports_dir.iterdir():
                if not rd.is_dir():
                    continue
                report = _BugReport(rd)
                if report.assessment is None:
                    continue
                report.status = status_data.get(report.bucket_hash)
                if not args.show_invalid and report.severity.lower().startswith("invalid"):
                    continue
                if not report.matches_severity(args.severity):
                    continue
                all_reports[pname][harness.path.name].append(report)

    if not all_reports:
        warn("No bug reports found")
        return

    total = 0
    for pname in sorted(all_reports):
        pr = all_reports[pname]
        project_path = root / pname
        project = Project(project_path)
        config = project.get_config()
        count = sum(len(v) for v in pr.values())
        total += count
        print(f"\n{Colors.CYAN}{pname}{Colors.END} ({count} reports) {Colors.DIM}{config.repo}{Colors.END}")
        for hname in sorted(pr):
            reports = pr[hname]
            by_sev = defaultdict(list)
            for r in reports:
                by_sev[r.severity].append(r)
            print(f"  {Colors.DIM}{hname}{Colors.END}")
            for sev in sorted(by_sev):
                sc = _severity_color(sev)
                rs = by_sev[sev]
                print(f"    {sc}{sev}{Colors.END}: {len(rs)}")
                for r in rs:
                    status_prefix = "[   ]"
                    if r.status:
                        lbl = r.status.lower()
                        if lbl == "dup":
                            status_prefix = f"{Colors.DIM}[dup]{Colors.END} "
                        elif lbl == "invalid":
                            status_prefix = f"{Colors.RED}[inv]{Colors.END} "
                        elif lbl == "wontfix":
                            status_prefix = f"{Colors.YELLOW}[nof]{Colors.END} "
                        elif r.status.startswith("http"):
                            status_prefix = f"{Colors.GREEN}[sub]{Colors.END} "
                        else:
                            status_prefix = f"{Colors.DIM}[{r.status}]{Colors.END} "
                    title = (r.assessment or {}).get("title", "")
                    print(f"      [{sc}{r.bucket_hash}{Colors.END}]{status_prefix} {title}")

    print(f"\n{Colors.GREEN}Total: {total} bug reports across {len(all_reports)} projects{Colors.END}")


# ═══════════════════════════════════════════════════════════════════════
# bugs replay  (was: replay)
# ═══════════════════════════════════════════════════════════════════════


def do_replay(args):
    from .._project import Project

    path = Path(args.path)
    project = Project(path)
    if not project.exists() or not project.has_config():
        fatal(f"Could not find project at {path.absolute()}")

    project.require_build_config()

    harness = select_harness(project, args.name)
    if harness is None:
        return

    asan = not args.no_asan
    if asan:
        ok("Using ASAN")

    image_tag = project.build_docker(use_asan=asan, override_commit=args.commit)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        tmpdir.mkdir(parents=True, exist_ok=True)
        shutil.copy(harness.harness_config(), tmpdir / "harness.json")
        shutil.copy(project.path / "config" / "info.json", tmpdir / "info.json")

        mounts = [
            (str(tmpdir.absolute()), "/fuzz/workspace"),
            (str(harness.campaign_dir().absolute()), "/fuzz/campaign"),
            (str(project.bugs_dir().absolute()), "/fuzz/bugs"),
        ]

        flags = ""
        if args.raw:
            flags += " --raw"
        if args.bug_hash:
            flags += f" --bug-hash {args.bug_hash}"
        if args.all:
            flags += " --all"
        if args.save is not None:
            flags += f" --save {args.save}"

        project.invoke(
            mounts=mounts,
            image=image_tag,
            cmd=f"cd /fuzz/workspace && stitchi inference replay /fuzz/campaign /fuzz/bugs{flags}",
        )


# ═══════════════════════════════════════════════════════════════════════
# bugs save  (was: save)
# ═══════════════════════════════════════════════════════════════════════


def do_save(args):
    from .._project import Project

    path = Path(args.path)
    project = Project(path)
    if not project.exists() or not project.has_config():
        fatal(f"Could not find project at {path.absolute()}")

    project.require_build_config()

    harness = select_harness(project, args.name)
    if harness is None:
        return

    image_tag = project.build_docker(use_asan=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        tmpdir.mkdir(parents=True, exist_ok=True)
        shutil.copy(harness.harness_config(), tmpdir / "harness.json")
        shutil.copy(project.path / "config" / "info.json", tmpdir / "info.json")

        mounts = [
            (str(tmpdir.absolute()), "/fuzz/workspace"),
            (str(harness.campaign_dir().absolute()), "/fuzz/campaign"),
            (str(project.bugs_dir().absolute()), "/fuzz/bugs"),
        ]

        flags = f" --save {args.save}"
        if args.bug_hash:
            flags += f" --bug-hash {args.bug_hash}"

        project.invoke(
            mounts=mounts,
            image=image_tag,
            cmd=f"cd /fuzz/workspace && stitchi inference save /fuzz/campaign /fuzz/bugs{flags}",
        )


# ═══════════════════════════════════════════════════════════════════════
# bugs triage  (was: triage)
# ═══════════════════════════════════════════════════════════════════════


def do_triage(args):
    from .._project import Project

    path = Path(args.path)
    project = Project(path)
    if not project.exists() or not project.has_config():
        fatal(f"Could not find project at {path.absolute()}")

    project.require_build_config()

    harness = select_harness(project, args.name)
    if harness is None:
        return

    check_env(["OPENAI_API_KEY"])

    env = [
        "OPENAI_API_KEY",
        "LANGSMITH_API_KEY",
        "LANGSMITH_PROJECT",
        "LANGSMITH_ENDPOINT",
        "LANGSMITH_TRACING",
    ]

    campaign_dir = harness.campaign_dir()
    buckets_dir = campaign_dir / "buckets"
    if not buckets_dir.exists() or not any(buckets_dir.iterdir()):
        warn("No crash buckets found. Run stitch fuzz first.")
        return

    watch_flag = " --watch" if args.watch else ""
    no_min_flag = " --no-minimize" if args.no_minimize else ""

    for use_asan, label in [(False, "non-ASAN"), (True, "ASAN")]:
        ok(f"Running {label} triage…")
        image_tag = project.build_docker(use_asan=use_asan)
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            tmpdir.mkdir(parents=True, exist_ok=True)
            shutil.copy(harness.harness_config(), tmpdir / "harness.json")
            shutil.copy(project.path / "config" / "info.json", tmpdir / "info.json")

            analysis_dir = harness.analysis_dir()
            mounts = [
                (str(tmpdir.absolute()), "/fuzz/workspace"),
                (str(buckets_dir.absolute()), "/fuzz/buckets"),
                (str(analysis_dir.absolute()), "/fuzz/analysis"),
            ]
            project.invoke(
                env=env,
                mounts=mounts,
                image=image_tag,
                cmd=(
                    "cd /fuzz/workspace &&"
                    " stitchi inference to-schema --meta harness.json --output test.json &&"
                    " stitchi build full test &&"
                    f" stitchi crash triage ./test /fuzz/buckets /fuzz/analysis --nproc {args.nproc}{watch_flag}{no_min_flag}"
                ),
            )


# ═══════════════════════════════════════════════════════════════════════
# bugs analyze  (was: analyze-crashes)
# ═══════════════════════════════════════════════════════════════════════


def do_analyze(args):
    from .._project import Project

    path = Path(args.path)
    project = Project(path)
    if not project.exists() or not project.has_config():
        fatal(f"Could not find project at {path.absolute()}")

    project.require_build_config()

    harness = select_harness(project, args.name)
    if harness is None:
        return

    crashes_dir = Path(args.crashes).absolute()
    if not crashes_dir.exists() or not crashes_dir.is_dir():
        fatal(f"Crashes directory does not exist: {crashes_dir}")

    output_dir = Path(args.output).absolute() if args.output else harness.analysis_dir().absolute()
    output_dir.mkdir(parents=True, exist_ok=True)

    ok(f"Harness: {harness.path.name}")
    ok(f"Crashes: {crashes_dir}")
    ok(f"Output:  {output_dir}")

    check_env(["OPENAI_API_KEY"])

    asan = not args.no_asan
    if asan:
        ok("Using ASAN")

    image_tag = project.build_docker(use_asan=asan)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        tmpdir.mkdir(parents=True, exist_ok=True)
        shutil.copy(harness.harness_config(), tmpdir / "harness.json")
        shutil.copy(project.path / "config" / "info.json", tmpdir / "info.json")

        project.invoke(
            env=["OPENAI_API_KEY"],
            mounts=[
                (str(tmpdir.absolute()), "/fuzz/workspace"),
                (str(crashes_dir), "/fuzz/crashes"),
                (str(output_dir), "/fuzz/output"),
            ],
            image=image_tag,
            cmd=(
                "cd /fuzz/workspace && export PYTHONUNBUFFERED=1 &&"
                " stitchi inference analyze-crashes --harness harness.json --crashes /fuzz/crashes --output /fuzz/output"
            ),
        )

    ok("Analysis complete!")
    ok(f"Results saved to: {output_dir}")


# ═══════════════════════════════════════════════════════════════════════
# bugs report  (was: report)
# ═══════════════════════════════════════════════════════════════════════


def do_report(args):
    from .._project import Project
    from ..data.project_info import ProjectInfo

    path = Path(args.path)
    project = Project(path)
    if not project.exists() or not project.has_config():
        fatal(f"Could not find project at {path.absolute()}")

    project.require_build_config()

    info = ProjectInfo.model_validate_json((project.path / "config" / "info.json").read_text())

    if args.testcase is None:
        fatal("--testcase is required")

    testcase_path = Path(args.testcase)
    testcase = testcase_path.read_text()

    crash_type = crash_type_reasoning = explanation = "unknown"
    assessment_path = Path(args.testcase.replace(".cpp", "_assessment.json"))
    if assessment_path.exists():
        a = json.loads(assessment_path.read_text())
        crash_type = a.get("crash_type", crash_type)
        crash_type_reasoning = a.get("crash_type_reasoning", crash_type_reasoning)
        explanation = a.get("explanation", explanation)

    custom_df = Path(args.custom_dockerfile).read_text() if args.custom_dockerfile else None
    base, tag = project.build_external_docker(
        override_commit=args.commit, testcase=testcase, verbose=args.verbose, custom_dockerfile=custom_df,
    )

    cmd = f'clang++-17 -fsanitize=address -g -O0 -o /fuzz/test /fuzz/testcase.cpp {" ".join(info.build_flags)}'
    ok(f"Running: {cmd}")

    has_tty = sys.stdin.isatty() and sys.stdout.isatty()
    docker_tty = ["-it"] if has_tty else []

    res = subprocess.run(
        ["docker", "run", "--platform", "linux/amd64", *docker_tty, "--rm",
         "-v", f"{testcase_path.absolute()}:/fuzz/testcase.cpp", tag, "bash", "-c", cmd],
    )
    if res.returncode != 0:
        fatal(f"Build failed with exit code {res.returncode}")

    ok("Building and running the testcase…")
    full_cmd = cmd + " && /fuzz/test"
    res = subprocess.run(
        ["docker", "run", "--platform", "linux/amd64", *docker_tty, "--rm",
         "-v", f"{testcase_path.absolute()}:/fuzz/testcase.cpp", tag, "bash", "-c", full_cmd],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )

    config = project.get_config()
    commit = config.commit if args.commit is None else args.commit
    commit_path = f"{config.repo}/commit/{commit}"

    report = f"""Hi, there is a potential bug in (component) reachable by (method).

This bug was reproduced on {commit_path}.

Severity: {crash_type}

{crash_type_reasoning}

### Description

{explanation}

### POC

**testcase.cpp**
```cpp
{testcase}
```

**stdout**
```text
{res.stdout.decode("utf-8", errors="replace")}
```

**stderr**
```text
{res.stderr.decode("utf-8", errors="replace")}
```

<details><summary>Steps to Reproduce</summary>

**Dockerfile**
```dockerfile
{base}
```

**Build Command**
```bash
{full_cmd}
```

**Reproduce**
```bash
docker build . -t repro --platform=linux/amd64
docker run -it --rm --platform linux/amd64 \\
    --mount type=bind,source="$(pwd)/testcase.cpp",target=/fuzz/testcase.cpp \\
    repro bash -c "{full_cmd.replace('"', chr(92) + '"')}"
```

</details>

---
*Discovered by STITCH. All reports are reviewed by a human before submission.*
"""
    print(report)


# ═══════════════════════════════════════════════════════════════════════
# bugs run  (was: run / bug)
# ═══════════════════════════════════════════════════════════════════════


def do_run(args):
    from .._project import Project

    path = Path(args.path)
    project = Project(path)
    if not project.exists() or not project.has_config():
        fatal(f"Could not find project at {path.absolute()}")

    project.require_build_config()

    asan = not args.no_asan
    if asan:
        ok("Using ASAN")

    custom_dockerfile = None
    if args.dockerfile:
        custom_dockerfile = Path(args.dockerfile)
        if not custom_dockerfile.exists():
            fatal(f"Custom Dockerfile not found: {custom_dockerfile}")
        ok(f"Using custom Dockerfile: {custom_dockerfile}")

    image_tag = project.build_docker(
        use_asan=asan,
        override_commit=args.commit,
        override_repo=args.repo,
        custom_dockerfile=custom_dockerfile,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        tmpdir.mkdir(parents=True, exist_ok=True)

        if args.info_json:
            info_json_path = Path(args.info_json)
            if not info_json_path.exists():
                fatal(f"Custom info.json not found: {info_json_path}")
            shutil.copy(info_json_path, tmpdir / "info.json")
        else:
            shutil.copy(project.path / "config" / "info.json", tmpdir / "info.json")
        shutil.copy(args.testcase, tmpdir / "test.cpp")

        mounts = [(str(tmpdir.absolute()), "/fuzz/workspace")]

        stdout_log = tmpdir / "bug_stdout.log"
        stderr_log = tmpdir / "bug_stderr.log"
        casr_san_log = tmpdir / "casr_san.json"
        casr_gdb_log = tmpdir / "casr_gdb.json"

        if args.debug:
            cmd = "cd /fuzz/workspace && stitchi build external test && pwndbg ./test"
        elif args.output:
            cmd = r"""cd /fuzz/workspace &&
BUILD_OK=0
stitchi build external test > >(tee -a bug_stdout.log) 2> >(tee -a bug_stderr.log >&2) || BUILD_OK=$?
if [ $BUILD_OK -ne 0 ]; then
    echo "=== COMPILATION FAILED (exit code $BUILD_OK) ===" >> bug_stderr.log
    exit $BUILD_OK
fi
./test > >(tee -a bug_stdout.log) 2> >(tee -a bug_stderr.log >&2)
TEST_EXIT=$?
if [ $TEST_EXIT -ne 0 ]; then
    timeout 10 casr-san --output casr_san.json -- ./test 2>/dev/null || true
    timeout 10 casr-gdb --output casr_gdb.json -- ./test 2>/dev/null || true
fi
exit $TEST_EXIT"""
        else:
            cmd = "cd /fuzz/workspace && stitchi build external test && ./test"

        result = project.invoke(mounts=mounts, image=image_tag, cmd=cmd, check=False)

        if args.output and not args.debug:
            stdout = stdout_log.read_text(errors="replace") if stdout_log.exists() else ""
            stderr = stderr_log.read_text(errors="replace") if stderr_log.exists() else ""

            casr_san = casr_gdb = None
            try:
                if casr_san_log.exists():
                    casr_san = json.loads(casr_san_log.read_text(errors="replace"))
            except Exception:
                pass
            try:
                if casr_gdb_log.exists():
                    casr_gdb = json.loads(casr_gdb_log.read_text(errors="replace"))
            except Exception:
                pass

            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(json.dumps({
                "stdout": stdout,
                "stderr": stderr,
                "returncode": result.returncode,
                "casr_san": casr_san,
                "casr_gdb": casr_gdb,
            }, indent=2))


# ═══════════════════════════════════════════════════════════════════════
# bugs exec  (was: execute)
# ═══════════════════════════════════════════════════════════════════════


def do_exec(args):
    from .._project import Project

    path = Path(args.path)
    project = Project(path)
    if not project.exists() or not project.has_config():
        fatal(f"Could not find project at {path.absolute()}")

    project.require_build_config()

    harness = select_harness(project, args.name)
    if harness is None:
        return

    testcase_path = Path(args.testcase)
    if not testcase_path.exists():
        fatal(f"Testcase {testcase_path} does not exist")

    asan = not args.no_asan
    if asan:
        ok("Using ASAN")

    image_tag = project.build_docker(use_asan=asan, override_commit=args.commit)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        tmpdir.mkdir(parents=True, exist_ok=True)
        shutil.copy(harness.harness_config(), tmpdir / "harness.json")
        shutil.copy(project.path / "config" / "info.json", tmpdir / "info.json")
        shutil.copy(testcase_path, tmpdir / "testcase.json")

        mounts = [
            (str(tmpdir.absolute()), "/fuzz/workspace"),
            (str(harness.campaign_dir().absolute()), "/fuzz/campaign"),
        ]

        project.invoke(
            mounts=mounts,
            image=image_tag,
            cmd=(
                "cd /fuzz/workspace &&"
                " stitchi inference to-schema --meta harness.json --output fuzzer.json &&"
                " stitchi build full fuzzer &&"
                " ./fuzzer -e testcase.json"
            ),
        )


# ═══════════════════════════════════════════════════════════════════════
# Registration
# ═══════════════════════════════════════════════════════════════════════


def _dispatch(args):
    if hasattr(args, "bugs_func"):
        return args.bugs_func(args)
    args._bugs_parser.print_help()
    return 1


def register(subparsers):
    p = subparsers.add_parser(
        "bugs",
        help="Bug discovery, replay, triage, and reporting",
        formatter_class=lambda prog: __import__("argparse").RawDescriptionHelpFormatter(prog, max_help_position=35),
    )
    sub = p.add_subparsers(dest="bugs_action", metavar="<action>")

    # ── list ──
    ls = sub.add_parser("list", help="List bug reports across projects")
    ls.add_argument("path", nargs="?", default=".", help="Directory containing projects")
    ls.add_argument("--show-invalid", action="store_true", help="Include bugs marked invalid")
    ls.add_argument("--severity", help="Filter by severity (crit, high, med, low, valid, invalid)")
    ls.set_defaults(bugs_func=do_list)

    # ── replay ──
    rp = sub.add_parser("replay", help="Replay crashes from a campaign")
    rp.add_argument("path", nargs="?", default=".", help="Project directory")
    rp.add_argument("-n", "--name", help="Harness name")
    rp.add_argument("-c", "--commit", help="Test on a specific commit")
    rp.add_argument("--raw", action="store_true", help="Print raw crash report")
    rp.add_argument("--no-asan", action="store_true", help="Disable ASAN")
    rp.add_argument("-b", "--bug-hash", help="Replay a specific bug by hash")
    rp.add_argument("-a", "--all", action="store_true", help="Replay all iterations")
    rp.add_argument("-s", "--save", type=int, default=None, help="Save testcase to disk")
    rp.set_defaults(bugs_func=do_replay)

    # ── save ──
    sv = sub.add_parser("save", help="Save a bug to disk")
    sv.add_argument("path", nargs="?", default=".", help="Project directory")
    sv.add_argument("-n", "--name", help="Harness name")
    sv.add_argument("-b", "--bug-hash", help="Bug hash to save")
    sv.add_argument("-s", "--save", type=int, default=0, help="Iteration to save")
    sv.set_defaults(bugs_func=do_save)

    # ── triage ──
    tr = sub.add_parser("triage", help="Run crash triage pipeline")
    tr.add_argument("path", nargs="?", default=".", help="Project directory")
    tr.add_argument("-n", "--name", help="Harness name")
    tr.add_argument("--watch", action="store_true", help="Continue watching for new crashes")
    tr.add_argument("--nproc", type=int, default=1, help="Number of processes")
    tr.add_argument("--no-minimize", action="store_true", help="Skip crash minimization")
    tr.set_defaults(bugs_func=do_triage)

    # ── analyze ──
    an = sub.add_parser("analyze", help="Analyze raw crash files")
    an.add_argument("path", nargs="?", default=".", help="Project directory")
    an.add_argument("--crashes", required=True, help="Path to crash files directory")
    an.add_argument("-n", "--name", help="Harness name")
    an.add_argument("-o", "--output", help="Output directory (default: <harness>/analysis)")
    an.add_argument("--no-asan", action="store_true", help="Disable ASAN")
    an.set_defaults(bugs_func=do_analyze)

    # ── report ──
    rpt = sub.add_parser("report", help="Generate an external bug report")
    rpt.add_argument("path", nargs="?", default=".", help="Project directory")
    rpt.add_argument("-t", "--testcase", help="Path to testcase.cpp")
    rpt.add_argument("-c", "--commit", help="Test on a specific commit")
    rpt.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    rpt.add_argument("--custom-dockerfile", help="Use a custom Dockerfile")
    rpt.set_defaults(bugs_func=do_report)

    # ── run ──
    rn = sub.add_parser("run", help="Compile and run a .cpp testcase")
    rn.add_argument("path", nargs="?", default=".", help="Project directory")
    rn.add_argument("-t", "--testcase", required=True, help="Path to testcase (.cpp)")
    rn.add_argument("-c", "--commit", help="Test on a specific commit")
    rn.add_argument("--repo", help="Override repository URL")
    rn.add_argument("--no-asan", action="store_true", help="Disable ASAN")
    rn.add_argument("-d", "--debug", action="store_true", help="Launch pwndbg debugger")
    rn.add_argument("-o", "--output", help="Write JSON results to this path")
    rn.add_argument("--dockerfile", help="Use a custom Dockerfile")
    rn.add_argument("--info-json", help="Use a custom info.json")
    rn.set_defaults(bugs_func=do_run)

    # ── exec ──
    ex = sub.add_parser("exec", help="Execute a raw graph testcase (JSON)")
    ex.add_argument("path", nargs="?", default=".", help="Project directory")
    ex.add_argument("-t", "--testcase", required=True, help="Path to testcase (JSON)")
    ex.add_argument("-n", "--name", help="Harness name")
    ex.add_argument("-c", "--commit", help="Test on a specific commit")
    ex.add_argument("--no-asan", action="store_true", help="Disable ASAN")
    ex.set_defaults(bugs_func=do_exec)

    p.set_defaults(func=_dispatch, _bugs_parser=p)
