from argparse import ArgumentParser

from . import gen
from . import loop
from . import builder
from . import llm2


def main():
    parser = ArgumentParser()
    subparsers = parser.add_subparsers()

    gen.register(subparsers)
    loop.register(subparsers)
    builder.register(subparsers)
    llm2.register(subparsers)
    args = parser.parse_args()
    if hasattr(args, 'func'):
        args.func(args)
    else:
        parser.print_help()

if __name__ == '__main__':
    main()
