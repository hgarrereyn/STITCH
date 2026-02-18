from .bucketizer import register as register_bucketizer
from .run_cover import register as register_cover
from .run_triage import register as register_triage
from .utils.crash_repro import register as register_summary


def _dispatch(args):
    args._crash_parser.print_help()
    return 1


def register(subparsers):
    parser = subparsers.add_parser("crash", help="Crash triage, bucketing, coverage, and summaries")
    crash_subparsers = parser.add_subparsers(dest="crash_action", metavar="<action>")

    register_triage(crash_subparsers, command_name="triage")
    register_cover(crash_subparsers, command_name="cover")
    register_bucketizer(crash_subparsers, command_name="bucketize")
    register_summary(crash_subparsers, command_name="summary")

    parser.set_defaults(func=_dispatch, _crash_parser=parser)
