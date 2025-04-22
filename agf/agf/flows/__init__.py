from . import find_objects, find_functions, gen_usage

def register(subparsers):
    flow_parser = subparsers.add_parser('flow')
    flow_subparsers = flow_parser.add_subparsers()
    find_objects.register(flow_subparsers)
    find_functions.register(flow_subparsers)
    gen_usage.register(flow_subparsers)
