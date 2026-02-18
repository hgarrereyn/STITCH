from .write import register as register_write


def _dispatch(args):
    args._util_parser.print_help()
    return 1


def register(subparsers):
    parser = subparsers.add_parser("util", help="Utility helpers")
    util_subparsers = parser.add_subparsers(dest="util_action", metavar="<action>")
    register_write(util_subparsers, command_name="write")
    parser.set_defaults(func=_dispatch, _util_parser=parser)
