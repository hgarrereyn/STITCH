from pathlib import Path
import subprocess
import shutil

from .utils.colors import Colors


def _generate_script(shell: str) -> str:
    exe = shutil.which("register-python-argcomplete")
    if exe is None:
        raise RuntimeError(
            "register-python-argcomplete is not installed. "
            "Install argcomplete first (poetry add argcomplete)."
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


def _confirm(prompt: str) -> bool:
    while True:
        ans = input(f"{prompt} [y/n]: ").strip().lower()
        if ans in {"y", "yes"}:
            return True
        if ans in {"n", "no"}:
            return False
        print("Please answer with y or n.")


def do_completion(args):
    shell = args.shell

    try:
        if args.action == "show":
            print(_generate_script(shell))
            return

        if shell == "fish":
            script = _generate_script("fish")
            dst = Path.home() / ".config" / "fish" / "completions" / "stitch.fish"
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(script)
            print(f"[{Colors.GREEN}+{Colors.END}] Installed fish completion at {dst}")
            return

        line = 'eval "$(register-python-argcomplete stitch)"'
        rc_path = Path.home() / (".zshrc" if shell == "zsh" else ".bashrc")
        changed = _append_if_missing(rc_path, line)
        if changed:
            print(f"[{Colors.GREEN}+{Colors.END}] Added completion init to {rc_path}")
        else:
            print(f"[{Colors.YELLOW}*{Colors.END}] Completion init already present in {rc_path}")
        print(
            f"[{Colors.CYAN}*{Colors.END}] Run {Colors.BOLD}source {rc_path}{Colors.END} "
            "or open a new shell."
        )
    except RuntimeError as e:
        print(f"[{Colors.RED}-{Colors.END}] {e}")
        raise SystemExit(1)


def do_install_completions(args):
    import os

    shell_guess = Path(os.getenv("SHELL", "")).name
    shell = shell_guess if shell_guess in {"bash", "zsh", "fish"} else None
    if shell is None:
        print(
            f"[{Colors.RED}-{Colors.END}] Could not detect shell from $SHELL. "
            "Set SHELL to bash, zsh, or fish and retry."
        )
        raise SystemExit(1)

    if shell == "fish":
        dst = Path.home() / ".config" / "fish" / "completions" / "stitch.fish"
        if not _confirm(f"Install fish completion to {dst}?"):
            print(f"[{Colors.YELLOW}*{Colors.END}] Skipped completion install.")
            return 1
        try:
            script = _generate_script("fish")
        except RuntimeError as e:
            print(f"[{Colors.RED}-{Colors.END}] {e}")
            raise SystemExit(1)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(script)
        print(f"[{Colors.GREEN}+{Colors.END}] Installed fish completion at {dst}")
        return 0

    rc_path = Path.home() / (".zshrc" if shell == "zsh" else ".bashrc")
    line = 'eval "$(register-python-argcomplete stitch)"'
    if not _confirm(f"Add stitch completion to {rc_path}?"):
        print(f"[{Colors.YELLOW}*{Colors.END}] Skipped completion install.")
        return 1
    changed = _append_if_missing(rc_path, line)
    if changed:
        print(f"[{Colors.GREEN}+{Colors.END}] Added completion init to {rc_path}")
    else:
        print(f"[{Colors.YELLOW}*{Colors.END}] Completion init already present in {rc_path}")
    print(
        f"[{Colors.CYAN}*{Colors.END}] Run {Colors.BOLD}source {rc_path}{Colors.END} "
        "or open a new shell."
    )
    return 0


def register(subparsers):
    import os

    shell_guess = Path(os.getenv("SHELL", "")).name
    default_shell = shell_guess if shell_guess in {"bash", "zsh", "fish"} else "zsh"

    sub = subparsers.add_parser(
        "completion",
        help="Show or install shell autocompletion",
    )
    sub.add_argument(
        "action",
        nargs="?",
        choices=["show", "install"],
        default="show",
        help="Print completion script or install it in your shell config",
    )
    sub.add_argument(
        "--shell",
        choices=["bash", "zsh", "fish"],
        default=default_shell,
        help="Target shell for completion script generation",
    )
    sub.set_defaults(func=do_completion)

    install = subparsers.add_parser(
        "install-completions",
        help="Install shell completions for your current shell",
    )
    install.set_defaults(func=do_install_completions)
