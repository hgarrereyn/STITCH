from __future__ import annotations

import argparse
import os
import sys


def build_parser() -> argparse.ArgumentParser:
    from .commands import (
        bugs,
        clear,
        completion,
        cover,
        dev,
        fuzz,
        gen,
        info,
        infer,
        init,
        test_cmd,
    )

    parser = argparse.ArgumentParser(
        prog="stitch",
        description="STITCH — autonomous fuzzing CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--no-color", action="store_true", help="Disable colored output")

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # ── Setup ────────────────────────────────────────────────────────
    init.register(sub)
    gen.register(sub)
    test_cmd.register(sub)

    # ── Core workflow ────────────────────────────────────────────────
    infer.register(sub)
    fuzz.register(sub)

    # ── Analysis ─────────────────────────────────────────────────────
    info.register(sub)
    cover.register(sub)
    bugs.register(sub)

    # ── Management ───────────────────────────────────────────────────
    clear.register(sub)

    # ── Development ──────────────────────────────────────────────────
    dev.register(sub)

    # ── Meta ─────────────────────────────────────────────────────────
    completion.register(sub)

    from ._complete import wire_completers

    wire_completers(parser)
    return parser


def main() -> int:
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
