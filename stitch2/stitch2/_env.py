import os

from ._colors import err, fatal


def check_env(keys: list[str]) -> None:
    missing = [k for k in keys if k not in os.environ]
    if missing:
        for k in missing:
            err(f"{k} is not set")
        fatal("Please set the required environment variables and try again")
