"""Shared harness-selection logic used by multiple commands."""

from __future__ import annotations

from typing import Optional

from ._colors import Colors, err
from ._project import Harness, Project


def select_harness(
    project: Project, name: Optional[str], *, hint: Optional[str] = None
) -> Optional[Harness]:
    """Resolve a single harness from *project*, prompting if ambiguous."""
    if name is None:
        harnesses = project.get_harnesses()
        if len(harnesses) == 0:
            err("No harnesses found")
            if hint:
                print(f"  {Colors.CYAN}Hint:{Colors.END} {hint}")
            return None
        if len(harnesses) == 1:
            return harnesses[0]
        err(
            f"Multiple harnesses found. "
            f"Please specify one ({Colors.CYAN}--name <name>{Colors.END})"
        )
        for h in harnesses:
            print(f"  {Colors.CYAN}*{Colors.END} {h.path.name}")
        return None

    harness = project.get_harness(name)
    if not harness.exists():
        err(f"Harness {name} does not exist")
        return None
    return harness
