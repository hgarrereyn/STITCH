import argparse

from . import init, infer, info, gen, dev, fuzz, triage, test, cover

def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()

    init.register(subparsers)
    infer.register(subparsers)
    info.register(subparsers)
    gen.register(subparsers)
    dev.register(subparsers)
    fuzz.register(subparsers)
    triage.register(subparsers)
    test.register(subparsers)
    cover.register(subparsers)

    args = parser.parse_args()
    if hasattr(args, 'func'):
        args.func(args)
    else:
        parser.print_help()

if __name__ == '__main__':
    main()
