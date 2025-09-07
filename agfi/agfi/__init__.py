import argparse

from .build.build import register as register_build
from .flows import register as register_flows
from .write import register as register_write
from .run_triage import register as register_run_triage
from .run_cover import register as register_run_cover
from .utils.crash_repro import register as register_crash_repro
from .incremental import register as register_incremental

def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()

    register_build(subparsers)
    register_flows(subparsers)
    register_write(subparsers)
    register_run_triage(subparsers)
    register_run_cover(subparsers)
    register_crash_repro(subparsers)
    register_incremental(subparsers)

    args = parser.parse_args()
    if hasattr(args, 'func'):
        args.func(args)
    else:
        parser.print_help()

if __name__ == '__main__':
    main()
