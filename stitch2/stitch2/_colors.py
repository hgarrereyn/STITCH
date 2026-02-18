import os
import sys


def _color_enabled() -> bool:
    if os.getenv("NO_COLOR"):
        return False
    if os.getenv("STITCH_NO_COLOR") == "1":
        return False
    return sys.stdout.isatty()


def _ansi(code: str) -> str:
    return f"\033[{code}m" if _color_enabled() else ""


class Colors:
    """ANSI color codes with automatic no-color support."""

    RED = _ansi("0;31")
    GREEN = _ansi("0;32")
    YELLOW = _ansi("1;33")
    BLUE = _ansi("0;34")
    PURPLE = _ansi("0;35")
    CYAN = _ansi("0;36")
    LIGHT_RED = _ansi("1;31")
    LIGHT_GREEN = _ansi("1;32")
    LIGHT_CYAN = _ansi("1;36")
    LIGHT_PURPLE = _ansi("1;35")
    LIGHT_YELLOW = _ansi("1;93")
    LIGHT_WHITE = _ansi("1;37")
    BOLD = _ansi("1")
    DIM = _ansi("2")
    UNDERLINE = _ansi("4")
    END = _ansi("0")


def ok(msg: str) -> None:
    print(f"[{Colors.GREEN}+{Colors.END}] {msg}")


def err(msg: str) -> None:
    print(f"[{Colors.RED}-{Colors.END}] {msg}")


def warn(msg: str) -> None:
    print(f"[{Colors.YELLOW}!{Colors.END}] {msg}")


def info(msg: str) -> None:
    print(f"[{Colors.CYAN}*{Colors.END}] {msg}")


def fatal(msg: str, *, code: int = 1) -> None:
    err(msg)
    raise SystemExit(code)
