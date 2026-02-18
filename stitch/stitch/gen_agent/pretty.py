"""
Terminal UI for the stitch gen agent.

Professional, minimal output with tasteful color. Built on Rich.
"""

import time
from typing import Dict, Optional

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

console = Console()

# ── Header ────────────────────────────────────────────────────────────────────


def gen_header(project: str, model: str):
    """Print the branded gen-agent header."""
    console.print()
    title = Text()
    title.append("STITCH", style="bold bright_cyan")
    title.append("  ·  ", style="dim")
    title.append("Build Configuration Agent", style="bold white")
    console.print(Panel(title, border_style="bright_cyan", padding=(0, 2)))
    console.print(f"  [dim]project[/dim]  {project}")
    console.print(f"  [dim]model[/dim]    {model}")
    console.print()


# ── Phase / step headers ─────────────────────────────────────────────────────


def phase(name: str, description: str = ""):
    """Print a phase header: ▶ name: description."""
    desc = f"[dim]:[/dim] {description}" if description else ""
    console.print(
        f"\n  [bold cyan]▶[/bold cyan] [bold white]{name}[/bold white]{desc}"
    )


def step_header(label: str):
    console.print(f"\n  [dim]┌[/dim] [bold]{label}[/bold]")


# ── Structured logging ────────────────────────────────────────────────────────


def success(msg: str):
    console.print(f"  [green]✓[/green] {msg}")


def error(msg: str):
    console.print(f"  [red]✗[/red] {msg}")


def warning(msg: str):
    console.print(f"  [yellow]![/yellow] {msg}")


def info(msg: str):
    console.print(f"  [dim]●[/dim] {msg}")


def reasoning(msg: str):
    console.print(f"  [magenta]◆[/magenta] [dim]{msg}[/dim]")


def action(msg: str):
    console.print(f"  [cyan]◆[/cyan] {msg}")


def file_entry(name: str, detail: str):
    console.print(f"    [dim]─[/dim] {name}  [dim]{detail}[/dim]")


def fatal(msg: str, *, code: int = 1):
    console.print(f"\n  [bold red]✗[/bold red] [red]{msg}[/red]")
    raise SystemExit(code)


# ── Code display ──────────────────────────────────────────────────────────────


def code_panel(
    code: str,
    language: str = "python",
    title: Optional[str] = None,
    *,
    theme: str = "monokai",
    line_numbers: bool = True,
    border: str = "dim",
):
    syntax = Syntax(
        code, language, theme=theme, line_numbers=line_numbers, word_wrap=True,
    )
    console.print(
        Panel(syntax, title=f"[bold]{title}[/bold]" if title else None,
              border_style=border, padding=(0, 1)),
    )


def error_panel(title: str, message: str, stdout: str = "", stderr: str = ""):
    console.print(
        Panel(
            message,
            title=f"[bold red]{title}[/bold red]",
            border_style="red",
            padding=(0, 1),
        )
    )
    if stdout.strip():
        code_panel(stdout, language="text", title="stdout", border="dim")
    if stderr.strip():
        code_panel(stderr, language="text", title="stderr", border="red")


# ── Summary ───────────────────────────────────────────────────────────────────


def gen_summary(usage_costs: Dict[str, float], elapsed: float):
    """Print the final gen-agent summary panel."""
    console.print()
    total_cost = sum(usage_costs.values())

    table = Table(show_header=False, box=None, padding=(0, 3))
    table.add_column(style="dim", justify="right")
    table.add_column(style="bold white")

    table.add_row("Time", f"{elapsed:.1f}s")
    table.add_row("Cost", f"${total_cost:.4f}")
    for task, cost in usage_costs.items():
        table.add_row(f"  {task}", f"${cost:.4f}")

    panel = Panel(
        table,
        title="[bold green]✓ Generation Complete[/bold green]",
        border_style="green",
        padding=(1, 2),
    )
    console.print(panel)
    console.print()
