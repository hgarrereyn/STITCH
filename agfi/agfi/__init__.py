import argparse

from .build.build import register as register_build
from .flows import register as register_flows
from .write import register as register_write
from .run_triage import register as register_run_triage

def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()

    register_build(subparsers)
    register_flows(subparsers)
    register_write(subparsers)
    register_run_triage(subparsers)

    args = parser.parse_args()
    if hasattr(args, 'func'):
        args.func(args)
    else:
        parser.print_help()

if __name__ == '__main__':
    main()
