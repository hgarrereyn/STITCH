import os

from .colors import Colors


def check_env(keys: list[str]):
    good = True
    for key in keys:
        if key not in os.environ:
            print(f'[{Colors.RED}-{Colors.END}] {key} is not set')
            good = False
    if not good:
        print(f'[{Colors.RED}-{Colors.END}] Please set the environment variables and try again')
        exit(1)
