import argparse

from .build.build import register as register_build
from .build.build import register_validate as register_validate
from .crash import register as register_crash
from .util import register as register_util
from .inference import register as register_inference

def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()

    register_build(subparsers)
    register_validate(subparsers)
    register_crash(subparsers)
    register_util(subparsers)
    register_inference(subparsers)

    args = parser.parse_args()
    if hasattr(args, 'func'):
        args.func(args)
    else:
        parser.print_help()

if __name__ == '__main__':
    main()
