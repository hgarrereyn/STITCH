from . import scan_objects, scan_functions, synth_functions, run, to_schema

def register(subparsers):
    zephyr_parser = subparsers.add_parser('zephyr')
    zephyr_subparsers = zephyr_parser.add_subparsers()
    run.register(zephyr_subparsers)
    scan_objects.register(zephyr_subparsers)
    scan_functions.register(zephyr_subparsers)
    synth_functions.register(zephyr_subparsers)
    to_schema.register(zephyr_subparsers)
