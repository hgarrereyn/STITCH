"""Argcomplete helpers for harness names and project paths."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List


def _list_harness_names(project_path: Path) -> List[str]:
    from ._project import Project

    project = Project(project_path)
    if not project.exists():
        return []
    try:
        return sorted(h.path.name for h in project.get_harnesses())
    except Exception:
        return []


def complete_project_path(prefix: str, **_kwargs) -> Iterable[str]:
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


def complete_harness_name(prefix: str, parsed_args, **_kwargs) -> Iterable[str]:
    path_value = getattr(parsed_args, "path", ".")
    names = _list_harness_names(Path(path_value))
    return [name for name in names if name.startswith(prefix)]


def wire_completers(parser) -> None:
    """Walk the parser tree and attach completers to known dest names."""
    import argparse

    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for sub in action.choices.values():
                wire_completers(sub)
            continue

        if action.dest == "path":
            action.completer = complete_project_path  # type: ignore[attr-defined]
        elif action.dest == "name" and "--name" in (action.option_strings or []):
            action.completer = complete_harness_name  # type: ignore[attr-defined]
        elif action.dest == "harness":
            action.completer = complete_harness_name  # type: ignore[attr-defined]
