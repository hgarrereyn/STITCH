from . import run
from . import to_schema

def register(subparsers):
    inc_parser = subparsers.add_parser('inc')
    inc_subparsers = inc_parser.add_subparsers()
    run.register(inc_subparsers)
    to_schema.register(inc_subparsers)
