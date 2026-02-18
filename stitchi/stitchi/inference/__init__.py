from . import (
    analyze_crashes,
    build_generators,
    campaign_info,
    data_classifier,
    distill_agent,
    fill,
    harness_info,
    looper,
    patcher,
    patcher_agent,
    replay,
    run,
    save,
    scan_functions,
    scan_objects,
    synth_functions,
    to_schema,
)

def register(subparsers, command_name: str = 'inference'):
    inference_parser = subparsers.add_parser(command_name, help='Inference-centric fuzzing workflows')
    inference_subparsers = inference_parser.add_subparsers()
    run.register(inference_subparsers)
    scan_objects.register(inference_subparsers)
    scan_functions.register(inference_subparsers)
    synth_functions.register(inference_subparsers)
    to_schema.register(inference_subparsers)
    data_classifier.register(inference_subparsers)
    build_generators.register(inference_subparsers)
    distill_agent.register(inference_subparsers)
    patcher.register(inference_subparsers)
    looper.register(inference_subparsers)
    fill.register(inference_subparsers)
    campaign_info.register(inference_subparsers)
    harness_info.register(inference_subparsers)
    replay.register(inference_subparsers)
    save.register(inference_subparsers)
    patcher_agent.register(inference_subparsers)
    analyze_crashes.register(inference_subparsers)