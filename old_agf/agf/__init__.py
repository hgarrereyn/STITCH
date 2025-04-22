import argparse

# from . import autoconf
from . import autoschema
from . import agents
# from .rag import rag
from . import write
from .runner import runner2
from .utils import ctags
from . import build
from .http import app

def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()

    # autoconf.register(subparsers)
    autoschema.register(subparsers)
    agents.register(subparsers)
    # rag.register(subparsers)
    write.register(subparsers)
    runner2.register(subparsers)
    ctags.register(subparsers)
    build.register(subparsers)
    app.register(subparsers)
    args = parser.parse_args()
    if hasattr(args, 'func'):
        args.func(args)
    else:
        parser.print_help()

if __name__ == '__main__':
    main()
