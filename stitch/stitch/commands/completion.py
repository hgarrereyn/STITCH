"""Shell completion management.

Merged from the old ``completion`` + ``install-completions`` commands.

Usage:
    stitch completion show       Print the completion script
    stitch completion install    Auto-detect shell and install
"""

import os
import shutil
import subprocess
from pathlib import Path

from .._colors import Colors, ok, warn, fatal


def _generate_script(shell: str) -> str:
    exe = shutil.which("register-python-argcomplete")
    if exe is None:
        raise RuntimeError(
            "register-python-argcomplete is not installed. "
            "Install argcomplete first (pip install argcomplete)."
        )
    cmd = [exe]
    if shell != "bash":
        cmd.extend(["--shell", shell])
    cmd.append("stitch")
    return subprocess.check_output(cmd, text=True)


def _append_if_missing(path: Path, line: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text() if path.exists() else ""
    if line in existing:
        return False
    with path.open("a") as f:
        if existing and not existing.endswith("\n"):
            f.write("\n")
        f.write(line + "\n")
    return True


def _detect_shell() -> str:
    guess = Path(os.getenv("SHELL", "")).name
    if guess in ("bash", "zsh", "fish"):
        return guess
    return "zsh"


def _confirm(prompt: str) -> bool:
    while True:
        ans = input(f"{prompt} [y/n]: ").strip().lower()
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False


def do_show(args):
    shell = args.shell or _detect_shell()
    try:
        print(_generate_script(shell))
    except RuntimeError as e:
        fatal(str(e))


def do_install(args):
    shell = args.shell or _detect_shell()

    try:
        if shell == "fish":
            dst = Path.home() / ".config" / "fish" / "completions" / "stitch.fish"
            if not _confirm(f"Install fish completion to {dst}?"):
                warn("Skipped")
                return 1
            script = _generate_script("fish")
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(script)
            ok(f"Installed fish completion at {dst}")
            return

        line = 'eval "$(register-python-argcomplete stitch)"'
        rc_path = Path.home() / (".zshrc" if shell == "zsh" else ".bashrc")

        if not _confirm(f"Add stitch completion to {rc_path}?"):
            warn("Skipped")
            return 1

        changed = _append_if_missing(rc_path, line)
        if changed:
            ok(f"Added completion init to {rc_path}")
        else:
            warn(f"Completion already present in {rc_path}")
        ok(f"Run {Colors.BOLD}source {rc_path}{Colors.END} or open a new shell.")
    except RuntimeError as e:
        fatal(str(e))


def _dispatch(args):
    if hasattr(args, "completion_func"):
        return args.completion_func(args)
    args._completion_parser.print_help()
    return 1


def register(subparsers):
    p = subparsers.add_parser("completion", help="Show or install shell autocompletion")
    sub = p.add_subparsers(dest="completion_action", metavar="<action>")

    default_shell = _detect_shell()

    sh = sub.add_parser("show", help="Print the completion script to stdout")
    sh.add_argument("--shell", choices=["bash", "zsh", "fish"], default=default_shell, help="Target shell")
    sh.set_defaults(completion_func=do_show)

    ins = sub.add_parser("install", help="Install completions for your current shell")
    ins.add_argument("--shell", choices=["bash", "zsh", "fish"], default=default_shell, help="Target shell")
    ins.set_defaults(completion_func=do_install)

    p.set_defaults(func=_dispatch, _completion_parser=p)
