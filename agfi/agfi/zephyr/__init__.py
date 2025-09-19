from . import scan_objects, scan_functions, synth_functions, run, to_schema, data_classifier, build_generators, distill_agent, patcher, looper, fill, campaign_info, harness_info, replay

def register(subparsers):
    zephyr_parser = subparsers.add_parser('zephyr')
    zephyr_subparsers = zephyr_parser.add_subparsers()
    run.register(zephyr_subparsers)
    scan_objects.register(zephyr_subparsers)
    scan_functions.register(zephyr_subparsers)
    synth_functions.register(zephyr_subparsers)
    to_schema.register(zephyr_subparsers)
    data_classifier.register(zephyr_subparsers)
    build_generators.register(zephyr_subparsers)
    distill_agent.register(zephyr_subparsers)
    patcher.register(zephyr_subparsers)
    looper.register(zephyr_subparsers)
    fill.register(zephyr_subparsers)
    campaign_info.register(zephyr_subparsers)
    harness_info.register(zephyr_subparsers)
    replay.register(zephyr_subparsers)