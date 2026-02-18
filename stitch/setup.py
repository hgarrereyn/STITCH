from __future__ import annotations

import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py
from setuptools.command.sdist import sdist as _sdist


THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent
PKG_ROOT = THIS_DIR / "stitch"
BUNDLE_ROOT = PKG_ROOT / "_bundled"

REQUIRED_PATHS = (
    "stitch_core",
    "stitch_libafl",
    "stitchi",
    "rust-toolchain.toml",
)


def _ignore_bundle_copy(_src: str, names: list[str]) -> set[str]:
    ignored = {
        ".git",
        ".github",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".venv",
        "venv",
        "dist",
        "node_modules",
        ".DS_Store",
    }
    for name in names:
        if name == "target" or name.endswith(".egg-info"):
            ignored.add(name)
    return ignored


def _require_repo_assets() -> None:
    missing = [p for p in REQUIRED_PATHS if not (REPO_ROOT / p).exists()]
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(f"Missing repository assets required for packaging: {joined}")


def _refresh_bundled_assets() -> None:
    if not all((REPO_ROOT / p).exists() for p in REQUIRED_PATHS):
        if BUNDLE_ROOT.exists():
            # Building from an sdist: assets are already bundled.
            return
        _require_repo_assets()
    if BUNDLE_ROOT.exists():
        shutil.rmtree(BUNDLE_ROOT)
    BUNDLE_ROOT.mkdir(parents=True, exist_ok=True)

    for dirname in ("stitch_core", "stitch_libafl", "stitchi"):
        shutil.copytree(
            REPO_ROOT / dirname,
            BUNDLE_ROOT / dirname,
            ignore=_ignore_bundle_copy,
        )

    shutil.copy2(REPO_ROOT / "rust-toolchain.toml", BUNDLE_ROOT / "rust-toolchain.toml")


class build_py(_build_py):
    def run(self) -> None:
        _refresh_bundled_assets()
        super().run()


class sdist(_sdist):
    def run(self) -> None:
        _refresh_bundled_assets()
        super().run()


setup(
    cmdclass={
        "build_py": build_py,
        "sdist": sdist,
    },
)
