import argparse

from .build.build import register as register_build
from .flows import register as register_flows

def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()

    register_build(subparsers)
    register_flows(subparsers)

    args = parser.parse_args()
    if hasattr(args, 'func'):
        args.func(args)
    else:
        parser.print_help()

if __name__ == '__main__':
    main()
