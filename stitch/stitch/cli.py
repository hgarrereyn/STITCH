from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable, List


def _list_harness_names(project_path: Path) -> List[str]:
    from .utils.project import Project

    project = Project(project_path)
    if not project.exists():
        return []
    try:
        return sorted(h.path.name for h in project.get_harnesses())
    except Exception:
        return []


def _complete_project_path(prefix: str, **_kwargs) -> Iterable[str]:
    if not prefix:
        prefix = "."
    p = Path(prefix)
    base = p.parent if p.parent != Path("") else Path(".")
    needle = p.name
    if not base.exists():
        return []
    return sorted(
        str(x) + ("/" if x.is_dir() else "")
        for x in base.iterdir()
        if x.is_dir() and x.name.startswith(needle)
    )


def _complete_harness_name(prefix: str, parsed_args, **_kwargs) -> Iterable[str]:
    path_value = getattr(parsed_args, "path", ".")
    names = _list_harness_names(Path(path_value))
    return [name for name in names if name.startswith(prefix)]


def _wire_completers(parser: argparse.ArgumentParser) -> None:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for sub in action.choices.values():
                _wire_completers(sub)
            continue

        if action.dest == "path":
            action.completer = _complete_project_path
        elif action.dest == "name" and "--name" in action.option_strings:
            action.completer = _complete_harness_name


def build_parser() -> argparse.ArgumentParser:
    # Keep imports lazy to minimize startup overhead and side effects.
    from . import (
        analyze_crashes,
        clear,
        completion,
        cover,
        dev,
        execute,
        fuzz,
        fuzzraw,
        gen,
        infer,
        info,
        init,
        monitor,
        multi,
        replay,
        report,
        run,
        save,
        status,
        stats,
        test,
        triage,
    )

    parser = argparse.ArgumentParser(
        prog="stitch",
        description="STITCH fuzzing CLI: project setup, inference, and fuzz orchestration",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI colors for this invocation",
    )
    subparsers = parser.add_subparsers(dest="command")

    init.register(subparsers)
    infer.register(subparsers)
    info.register(subparsers)
    gen.register(subparsers)
    dev.register(subparsers)
    fuzz.register(subparsers)
    fuzzraw.register(subparsers)
    triage.register(subparsers)
    test.register(subparsers)
    cover.register(subparsers)
    stats.register(subparsers)
    replay.register(subparsers)
    run.register(subparsers)
    save.register(subparsers)
    report.register(subparsers)
    status.register(subparsers)
    monitor.register(subparsers)
    multi.register(subparsers)
    execute.register(subparsers)
    clear.register(subparsers)
    analyze_crashes.register(subparsers)
    completion.register(subparsers)

    _wire_completers(parser)
    return parser


def main() -> int:
    import os
    import sys

    # Must happen before subcommand modules import Colors.
    if "--no-color" in sys.argv:
        os.environ["STITCH_NO_COLOR"] = "1"

    parser = build_parser()

    try:
        import argcomplete  # type: ignore

        argcomplete.autocomplete(parser)
    except Exception:
        pass

    args = parser.parse_args()

    if hasattr(args, "func"):
        result = args.func(args)
        return 0 if result is None else int(result)

    parser.print_help()
    return 1
