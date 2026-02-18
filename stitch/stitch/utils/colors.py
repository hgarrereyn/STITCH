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

    BLACK = _ansi("0;30")
    RED = _ansi("0;31")
    GREEN = _ansi("0;32")
    BROWN = _ansi("0;33")
    BLUE = _ansi("0;34")
    PURPLE = _ansi("0;35")
    CYAN = _ansi("0;36")
    LIGHT_GRAY = _ansi("0;37")
    DARK_GRAY = _ansi("1;30")
    LIGHT_RED = _ansi("1;31")
    LIGHT_GREEN = _ansi("1;32")
    YELLOW = _ansi("1;33")
    LIGHT_BLUE = _ansi("1;34")
    LIGHT_PURPLE = _ansi("1;35")
    LIGHT_CYAN = _ansi("1;36")
    LIGHT_WHITE = _ansi("1;37")
    LIGHT_YELLOW = _ansi("1;93")
    BOLD = _ansi("1")
    FAINT = _ansi("2")
    ITALIC = _ansi("3")
    UNDERLINE = _ansi("4")
    BLINK = _ansi("5")
    NEGATIVE = _ansi("7")
    CROSSED = _ansi("9")
    END = _ansi("0")
    WHITE = _ansi("97")
    DIM = _ansi("2")
