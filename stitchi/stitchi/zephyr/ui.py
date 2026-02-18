"""
Clean, modern terminal UI for the STITCH inference pipeline.

Provides phase headers, progress bars, structured logging, and
parallel execution wrappers with live per-item status tracking.
"""

import time
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Generator

from rich.console import Console, Group
from rich.progress import (
    Progress,
    SpinnerColumn,
    BarColumn,
    TextColumn,
    MofNCompleteColumn,
    TimeElapsedColumn,
    TaskProgressColumn,
)
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text
from rich.table import Table

console = Console()

# ── Phase / section headers ──────────────────────────────────────────────────

def pipeline_header():
    """Print the branded pipeline header."""
    console.print()
    title = Text()
    title.append("STITCH", style="bold bright_cyan")
    title.append("  ·  ", style="dim")
    title.append("Inference Pipeline", style="bold white")
    console.print(Panel(
        title,
        border_style="bright_cyan",
        padding=(0, 2),
    ))
    console.print()


def phase(name: str, description: str):
    """Print a phase header: ▶ name: description."""
    console.print()
    console.print(
        f"  [bold cyan]▶[/bold cyan] [bold white]{name}[/bold white][dim]:[/dim] {description}"
    )


def phase_result(message: str, elapsed: float):
    """Print a phase completion summary with timing."""
    console.print(
        f"  [bold green]✓[/bold green] {message}"
        f"  [dim]{elapsed:.1f}s[/dim]"
    )


def step_header(label: str):
    """Print a step header within a phase (e.g. synthesis iteration)."""
    console.print(f"\n  [bold dim]┌[/bold dim] [bold]{label}[/bold]")


# ── Structured logging ───────────────────────────────────────────────────────

_MODE_STYLES = {
    "VALID":     ("green",        "✓"),
    "ERROR":     ("red",          "✗"),
    "WARN":      ("yellow",       "!"),
    "SCAN":      ("cyan",         "◆"),
    "SYNTH":     ("magenta",      "◆"),
    "FILL":      ("yellow",       "◆"),
    "CLASSIFY":  ("bright_blue",  "◆"),
    "GENERATOR": ("bright_green", "◆"),
    "INFO":      ("dim",          "●"),
}


def log(mode: str, msg: str):
    """Print a structured log line with a mode-colored icon."""
    style, icon = _MODE_STYLES.get(mode, ("dim", "●"))
    console.print(f"  [{style}]{icon}[/{style}] {msg}")


def success(msg: str):
    console.print(f"  [green]✓[/green] {msg}")


def error(msg: str):
    console.print(f"  [red]✗[/red] {msg}")


def info(msg: str):
    console.print(f"  [dim]●[/dim] {msg}")


def warning(msg: str):
    console.print(f"  [yellow]![/yellow] {msg}")


# ── Progress bars ────────────────────────────────────────────────────────────

def _make_progress() -> Progress:
    return Progress(
        TextColumn("  "),
        SpinnerColumn("dots", style="cyan"),
        TextColumn("{task.description}"),
        BarColumn(
            bar_width=30,
            style="bar.back",
            complete_style="cyan",
            finished_style="green",
        ),
        TaskProgressColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    )


@contextmanager
def progress_bar(label: str, total: int) -> Generator:
    """Context manager yielding an advance(n=1) callback for manual progress."""
    with _make_progress() as progress:
        task = progress.add_task(label, total=total)

        def advance(n: int = 1):
            progress.advance(task, n)

        yield advance


# ── Token progress tracking ──────────────────────────────────────────────────

@dataclass
class TokenProgress:
    """Thread-safe-ish mutable counter for live token display.

    Written by call_llm() during streaming; read by the spinner renderer
    on every refresh tick.  Atomic-enough for display purposes since each
    field is a single int assignment (GIL-protected on CPython).
    """
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


# ── Inline spinner for blocking operations ───────────────────────────────────

class _SpinnerLine:
    """Single-line renderable: indented spinner + message with Rich markup."""

    def __init__(self, message: str, token_progress: Optional[TokenProgress] = None):
        self._message = Text.from_markup(message)
        self._spinner = Spinner("dots", style="cyan")
        self._tp = token_progress

    def __rich_console__(self, console, options):
        frame = self._spinner.render(time.monotonic())
        parts = ["  ", frame, " ", self._message]
        if self._tp is not None and (self._tp.input_tokens or self._tp.output_tokens):
            tok = Text.from_markup(
                f"  [red]{_fmt_tokens(self._tp.input_tokens)} in[/red] › [green]{_fmt_tokens(self._tp.output_tokens)} out[/green]"
            )
            parts.append(tok)
        yield Text.assemble(*parts)


def _token_summary(tp: TokenProgress) -> str:
    return f"  [dim]tokens:[/dim] [red]{_fmt_tokens(tp.input_tokens)} in[/red] › [green]{_fmt_tokens(tp.output_tokens)} out[/green]"


@contextmanager
def spinner(message: str, token_progress: Optional[TokenProgress] = None) -> Generator:
    """Show an animated spinner while a blocking operation runs.

    The spinner line is removed when the context exits so the caller
    can print a result line in its place.
    """
    display = _SpinnerLine(message, token_progress)
    with Live(display, console=console, refresh_per_second=12, transient=True):
        yield
    if token_progress is not None and (token_progress.input_tokens or token_progress.output_tokens):
        console.print(_token_summary(token_progress))


# ── Per-item live tracker ────────────────────────────────────────────────────

class _ItemTracker:
    """Rich renderable showing per-item status with animated spinners."""

    PENDING = 0
    RUNNING = 1
    SUCCESS = 2
    ERROR = 3

    def __init__(self, labels: List[str]):
        self.labels = labels
        self.states = [self.PENDING] * len(labels)
        self._spinner = Spinner("dots", style="cyan")

    def __rich_console__(self, console, options):
        lines = []
        for i, label in enumerate(self.labels):
            state = self.states[i]
            if state == self.PENDING:
                lines.append(Text.assemble(
                    "  ", Text("○", style="dim"), " ", Text(label, style="dim"),
                ))
            elif state == self.RUNNING:
                frame = self._spinner.render(time.monotonic())
                lines.append(Text.assemble("  ", frame, " ", label))
            elif state == self.SUCCESS:
                lines.append(Text.assemble(
                    "  ", Text("✓", style="bold green"), " ", Text(label, style="green"),
                ))
            elif state == self.ERROR:
                lines.append(Text.assemble(
                    "  ", Text("✗", style="bold red"), " ", Text(label, style="red"),
                ))
        yield Group(*lines)


def _smash_fn(args) -> Any:
    fn = args[0]
    return fn(*args[1])


def _smash_fn_tracked(args) -> Any:
    """Like _smash_fn but marks the tracker item as RUNNING on entry."""
    idx, tracker, fn, fn_args = args
    tracker.states[idx] = _ItemTracker.RUNNING
    return fn(*fn_args)


# ── Parallel execution ───────────────────────────────────────────────────────

def parallel_run(
    threads: int,
    func: Any,
    inputs: List[Any],
    label: str = "Processing",
    item_labels: Optional[List[str]] = None,
    ok: Optional[Callable] = None,
) -> List[Any]:
    """Run *func* over *inputs* in parallel with visual progress.

    If *item_labels* is provided, each work item gets its own line with a
    spinner that resolves to ✓ or ✗ (like ``docker push`` layer progress).
    Otherwise falls back to a single progress bar.

    *ok* is an optional ``ok(raw_result) -> bool`` callback that determines
    whether a completed item is shown as ✓ (True) or ✗ (False).
    Only used with per-item display.

    Returns a **flattened** list (each func call is expected to return a list).
    """
    if not inputs:
        return []

    total = len(inputs)
    results: list = [None] * total

    if item_labels is not None:
        _run_with_item_tracker(threads, func, inputs, item_labels, ok, results)
    else:
        _run_with_progress_bar(threads, func, inputs, label, results)

    return [item for sublist in results for item in sublist]


def parallel_run_raw(
    threads: int,
    func: Any,
    inputs: List[Any],
    label: str = "Processing",
    item_labels: Optional[List[str]] = None,
    ok: Optional[Callable] = None,
) -> List[Any]:
    """Like *parallel_run* but returns raw (non-flattened) results."""
    if not inputs:
        return []

    total = len(inputs)
    results: list = [None] * total

    if item_labels is not None:
        _run_with_item_tracker(threads, func, inputs, item_labels, ok, results)
    else:
        _run_with_progress_bar(threads, func, inputs, label, results)

    return results


def _run_with_item_tracker(
    threads: int,
    func: Any,
    inputs: List[Any],
    item_labels: List[str],
    ok: Optional[Callable],
    results: list,
):
    """Execute tasks showing per-item spinner → ✓/✗ display."""
    tracker = _ItemTracker(item_labels)

    with Live(tracker, console=console, refresh_per_second=12, transient=False):
        with ThreadPoolExecutor(max_workers=threads) as executor:
            future_to_idx = {}
            for idx, inp in enumerate(inputs):
                future = executor.submit(
                    _smash_fn_tracked, (idx, tracker, func, inp),
                )
                future_to_idx[future] = idx

            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    result = future.result()
                    results[idx] = result
                    if ok is not None and not ok(result):
                        tracker.states[idx] = _ItemTracker.ERROR
                    else:
                        tracker.states[idx] = _ItemTracker.SUCCESS
                except Exception:
                    tracker.states[idx] = _ItemTracker.ERROR
                    raise


def _run_with_progress_bar(
    threads: int,
    func: Any,
    inputs: List[Any],
    label: str,
    results: list,
):
    """Execute tasks showing a single progress bar."""
    total = len(inputs)

    with _make_progress() as progress:
        task = progress.add_task(label, total=total)

        with ThreadPoolExecutor(max_workers=threads) as executor:
            future_to_idx = {}
            for idx, inp in enumerate(inputs):
                future = executor.submit(_smash_fn, (func, inp))
                future_to_idx[future] = idx

            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                results[idx] = future.result()
                progress.advance(task)


# ── Pipeline summary ─────────────────────────────────────────────────────────

def pipeline_summary(meta, elapsed: float, usage):
    """Print the final inference summary panel."""
    console.print()

    costs = usage.cost_by_task()
    total_cost = sum(costs.values())

    table = Table(show_header=False, box=None, padding=(0, 3))
    table.add_column(style="dim", justify="right")
    table.add_column(style="bold white")
    table.add_column(style="dim", justify="right")
    table.add_column(style="bold white")

    table.add_row(
        "Objects", str(len(meta.objects)),
        "Endpoints", str(len(meta.endpoints)),
    )
    table.add_row(
        "Functions", str(len(meta.functions)),
        "Generators", str(len(meta.generators)),
    )
    table.add_row(
        "Time", f"{elapsed:.1f}s",
        "Cost", f"${total_cost:.2f}",
    )

    panel = Panel(
        table,
        title="[bold green]✓ Inference Complete[/bold green]",
        border_style="green",
        padding=(1, 2),
    )
    console.print(panel)
    console.print()
