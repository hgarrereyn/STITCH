import argparse

from . import (
    init,
    infer,
    info,
    gen,
    dev,
    fuzz,
    fuzzraw,
    triage,
    test,
    cover,
    stats,
    replay,
    run,
    save,
    report,
    status,
    multi,
    execute,
    clear,
)


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()

    init.register(subparsers)
    infer.register(subparsers)
    info.register(subparsers)
    gen.register(subparsers)
    dev.register(subparsers)
    fuzz.register(subparsers)
    fuzzraw.register(subparsers)
    triage.register(subparsers)
    test.register(subparsers)
    cover.register(subparsers)
    stats.register(subparsers)
    replay.register(subparsers)
    run.register(subparsers)
    save.register(subparsers)
    report.register(subparsers)
    status.register(subparsers)
    multi.register(subparsers)
    execute.register(subparsers)
    clear.register(subparsers)

    args = parser.parse_args()
    if hasattr(args, 'func'):
        args.func(args)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
