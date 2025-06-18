from . import find_functions, find_objects, full, generate_stubs, generate_templates, to_schema, generate_hints, llm_minimize

def register(subparsers):
    flow_parser = subparsers.add_parser('flow')
    flow_subparsers = flow_parser.add_subparsers()
    find_objects.register(flow_subparsers)
    find_functions.register(flow_subparsers)
    full.register(flow_subparsers)
    generate_stubs.register(flow_subparsers)
    generate_templates.register(flow_subparsers)
    to_schema.register(flow_subparsers)
    generate_hints.register(flow_subparsers)
    llm_minimize.register(flow_subparsers)
