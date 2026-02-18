"""Project info command – merges the old ``info`` and ``stats`` commands."""

from pathlib import Path
import json
from typing import Dict, List, Optional, Set, Tuple
from collections import defaultdict

from .._colors import Colors, ok, err, warn, info as info_msg
from .._project import Project, ProjectConfig
from ..data.metadata import MODEL_COST


# ── Constructability analysis (from old stats.py) ────────────────────


def _analyze_constructability(
    validated_endpoints: List[dict],
    object_types: Dict[str, int],
    max_depth: int,
) -> Tuple[List[str], List[Tuple[str, str]], Set[str]]:
    endpoint_inputs: Dict[str, List[str]] = {}
    endpoint_outputs: Dict[str, List[str]] = {}
    types_seen: Set[str] = set()

    for ep in validated_endpoints:
        name = ep.get("name", "")
        in_types: List[str] = []
        for inp in ep.get("inputs", []) or []:
            tname = inp.get("type") or inp.get("name") or "" if isinstance(inp, dict) else str(inp)
            if tname:
                in_types.append(tname)
                types_seen.add(tname)

        out_types: List[str] = []
        for out in ep.get("outputs", []) or []:
            tname = out.get("type") or out.get("name") or "" if isinstance(out, dict) else str(out)
            if tname:
                out_types.append(tname)
                types_seen.add(tname)

        endpoint_inputs[name] = in_types
        endpoint_outputs[name] = out_types

    producers: Dict[str, List[str]] = defaultdict(list)
    for ep_name, outs in endpoint_outputs.items():
        for t in outs:
            producers[t].append(ep_name)

    memo: Dict[Tuple[str, int], bool] = {}
    visiting: Set[Tuple[str, int]] = set()

    def type_ok(tname: str, depth: int) -> bool:
        if depth == 0:
            return False
        key = (tname, depth)
        if key in memo:
            return memo[key]
        if key in visiting:
            memo[key] = False
            return False
        visiting.add(key)
        ok_flag = False
        for ep_name in producers.get(tname, []):
            if all(type_ok(it, depth - 1) for it in endpoint_inputs.get(ep_name, [])):
                ok_flag = True
                break
        visiting.remove(key)
        memo[key] = ok_flag
        return ok_flag

    all_types = set(object_types.keys()) | types_seen
    missing = {t for t in all_types if not type_ok(t, max_depth)}

    constructable: List[str] = []
    unconstructable: List[Tuple[str, str]] = []
    for ep_name, in_types in endpoint_inputs.items():
        miss = [t for t in in_types if t in missing]
        if miss:
            unconstructable.append((ep_name, f"Missing constructors for: {', '.join(sorted(set(miss)))}"))
        else:
            constructable.append(ep_name)

    return constructable, unconstructable, missing


def _print_harness_stats(harness_data: dict, max_depth: int) -> None:
    objects = harness_data.get("objects", []) or []
    endpoints = harness_data.get("endpoints", []) or []

    info_msg(f"Objects: {Colors.CYAN}{len(objects)}{Colors.END}")
    info_msg(f"Endpoints: {Colors.GREEN}{len(endpoints)}{Colors.END}")

    object_names = []
    for obj in objects:
        name = obj.get("type") or obj.get("name") or "" if isinstance(obj, dict) else str(obj)
        if name:
            object_names.append(name)
    object_types = {name: i for i, name in enumerate(object_names)}

    cons, uncons, missing = _analyze_constructability(endpoints, object_types, max_depth)
    print()
    info_msg("Constructability analysis:")
    print(f"  {Colors.GREEN}Constructable: {len(cons)}{Colors.END}")
    print(f"  {Colors.RED}Unconstructable: {len(uncons)}{Colors.END}")

    if uncons:
        print()
        for ep_name, reason in uncons:
            print(f"  {Colors.RED}•{Colors.END} {ep_name}: {reason}")

    if missing:
        print()
        warn(f"Types without constructors (depth={max_depth}):")
        for t in sorted(missing):
            print(f"    - {t}")


# ── Project overview ─────────────────────────────────────────────────


def _check(label: str, passed: bool, hint: Optional[str] = None) -> None:
    if passed:
        print(f"  {Colors.GREEN}✓{Colors.END} {label}")
    else:
        msg = f"  {Colors.RED}✗{Colors.END} {label}"
        if hint:
            msg += f"  {Colors.DIM}({hint}){Colors.END}"
        print(msg)


def _print_project_overview(project: Project, config: ProjectConfig) -> None:
    print(f"{Colors.BOLD}Project:{Colors.END} {Colors.CYAN}{config.name}{Colors.END}")

    if config.local_path:
        print(f"  Source: {Colors.DIM}{config.local_path}{Colors.END}")
    elif config.repo:
        print(f"  Source: {Colors.DIM}{config.repo}{Colors.END}")

    if config.commit:
        print(f"  Commit: {Colors.DIM}{config.commit[:12]}{Colors.END}")

    if config.model:
        print(f"  Model:  {Colors.DIM}{config.model}{Colors.END}")

    has_build = project.has_build_config()
    harnesses = project.get_harnesses()
    campaigns = []
    for h in harnesses:
        reports_dir = h.path / "campaign" / "reports"
        if reports_dir.is_dir() and any(reports_dir.iterdir()):
            campaigns.append(h)

    print(f"\n{Colors.BOLD}Pipeline status:{Colors.END}")
    _check("Initialized", True)
    _check("Build configured", has_build, hint="run stitch gen" if not has_build else None)

    if has_build:
        info_path = project.path / "config" / "info.json"
        if info_path.exists():
            try:
                from ..data.project_info import ProjectInfo
                pi = ProjectInfo.model_validate_json(info_path.read_text())
                parts = []
                if pi.codegen.headers:
                    parts.append(f"{len(pi.codegen.headers)} header(s)")
                if pi.build_flags:
                    parts.append(f"{len(pi.build_flags)} build flag(s)")
                if parts:
                    print(f"    {Colors.DIM}{', '.join(parts)}{Colors.END}")
            except Exception:
                pass

    has_harness = len(harnesses) > 0
    _check(
        f"Harness inferred ({len(harnesses)})" if has_harness else "Harness inferred",
        has_harness,
        hint="run stitch infer" if not has_harness else None,
    )
    _check(
        f"Fuzz campaigns ({len(campaigns)})" if campaigns else "Fuzz campaigns",
        len(campaigns) > 0,
        hint="run stitch fuzz" if not campaigns else None,
    )
    print()


# ── Main info command ────────────────────────────────────────────────


def do_info(args):
    path = Path(args.path)
    project = Project(path)
    if not project.exists() or not project.has_config():
        err(f"Could not find project at {path.absolute()}")
        return

    config = project.get_config()
    _print_project_overview(project, config)

    harnesses = project.get_harnesses()
    if not harnesses:
        return

    ok(f"Found {Colors.BOLD}{len(harnesses)}{Colors.END} harness(es)")

    # If --harness is given, show detailed stats for that harness only
    if args.harness:
        harness = project.get_harness(args.harness)
        if not harness.exists():
            err(f'Harness "{args.harness}" not found')
            return
        hf = harness.harness_config()
        if not hf.exists():
            err(f'Harness config not found for "{args.harness}"')
            return
        try:
            data = json.loads(hf.read_text())
        except Exception as e:
            err(f"Error reading harness file: {e}")
            return
        print(f"\n{Colors.BOLD}Harness: {Colors.CYAN}{args.harness}{Colors.END}")
        _print_harness_stats(data, args.depth)
        return

    # Otherwise, show overview for all harnesses
    for harness in harnesses:
        name = harness.path.name
        print(f"\n{Colors.BOLD}Harness: {Colors.CYAN}{name}{Colors.END}")

        hf = harness.harness_config()
        if hf.exists():
            try:
                hd = json.loads(hf.read_text())
                parts = []
                for key, label in [
                    ("objects", "objects"),
                    ("functions", "functions"),
                    ("endpoints", "endpoints"),
                    ("generators", "generators"),
                    ("data_types", "data types"),
                ]:
                    n = len(hd.get(key, []) or [])
                    parts.append(f"{label}: {Colors.LIGHT_CYAN}{n}{Colors.END}")
                print(f"  {Colors.DIM}|{Colors.END} " + f" {Colors.DIM}|{Colors.END} ".join(parts))
            except (json.JSONDecodeError, OSError):
                pass

        total_cost = harness.cost()
        if total_cost > 0:
            ok(f"Inference cost: {Colors.GREEN}${total_cost:.4f}{Colors.END}")
        else:
            print(f"  {Colors.DIM}No cost information available{Colors.END}")

        campaign_dir = harness.campaign_dir()
        reports_dir = campaign_dir / "reports"
        if not reports_dir.exists():
            print(f"  {Colors.DIM}No campaign found{Colors.END}")
            continue

        reports = []
        for rd in reports_dir.iterdir():
            if rd.is_dir() and (rd / "info.json").exists():
                try:
                    reports.append((rd, json.loads((rd / "info.json").read_text())))
                except Exception:
                    continue

        if not reports:
            print(f"  {Colors.DIM}No reports found{Colors.END}")
            continue

        reports.sort(key=lambda x: x[1].get("harness_revision", 0))
        ok(f"{len(reports)} campaign report(s):")

        for rd, crash_info in reports:
            rev = crash_info.get("harness_revision", "?")
            summary = crash_info.get("summary", "No summary")
            print(f"    {Colors.CYAN}{rd.name}{Colors.END}  rev {rev}  {Colors.DIM}{summary}{Colors.END}")

            assessment_file = rd / "assessment.json"
            if assessment_file.exists():
                try:
                    ad = json.loads(assessment_file.read_text())
                    crash_type = ad.get("crash_type", "unknown")
                    can_patch = ad.get("can_patch_fuzzer", False)
                    pc = Colors.GREEN if can_patch else Colors.RED
                    print(f"      Assessment: {Colors.CYAN}{crash_type}{Colors.END}  patchable: {pc}{can_patch}{Colors.END}")
                except Exception:
                    pass


def register(subparsers):
    p = subparsers.add_parser(
        "info",
        help="Display project info and harness statistics",
    )
    p.add_argument("path", nargs="?", default=".", help="Project directory")
    p.add_argument(
        "--harness",
        help="Show detailed constructability stats for a specific harness",
    )
    p.add_argument(
        "--depth",
        type=int,
        default=6,
        help="Max recursion depth for constructability analysis",
    )
    p.set_defaults(func=do_info)
