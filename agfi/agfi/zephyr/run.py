from typing import List, Any
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from .data import ObjectInfo, FunctionInfo, FunctionImplError, FunctionImpl
from .pretty import print_functions, pretty_print_error

from ..utils.colors import Colors
from ..data.metadata import UsageTracker, UsageTokens
from ..data.project_info import ProjectInfo
from .data import ProjectMetadata

from .scan_objects import scan_objects
from .scan_functions import scan_functions, scan_function_names
from .synth_functions import synth_functions, synth_functions_with_errors, incremental_synthesis_loop
from .validation import run_validate_object, run_validate_function_name, run_validate_stub

from .util import parallel_run, log

SYNTH_RETRIES = 3


def main(args):
    info = ProjectInfo.model_validate_json(open(args.info).read())

    code = open(args.header).read()
    usage = UsageTracker()

    log('INFO', f"Scanning header for objects")
    objects = scan_objects(code, usage)
    log('SCAN', f"Found {len(objects)} objects")

    log('INFO', f"Validating {len(objects)} objects")
    objects: List[ObjectInfo] = parallel_run(args.threads, run_validate_object, [(info, o) for o in objects])
    log('SCAN', f"Validated {len(objects)} objects")

    log('INFO', f"Scanning header for function names")
    function_names = scan_function_names(code, usage)
    log('SCAN', f"Found {len(function_names)} function names")

    log('INFO', f"Validating {len(function_names)} function names")
    function_names = parallel_run(args.threads, run_validate_function_name, [(info, f) for f in function_names])
    log('SCAN', f"Validated {len(function_names)} function names")

    log('INFO', f"Scanning header for functions")
    functions = scan_functions(code, function_names, usage)
    log('SCAN', f"Found {len(functions)} functions")

    valid_stubs, failed_stubs = incremental_synthesis_loop(objects, functions, usage, args.threads, info)
    log('SYNTH', f"Synthesized {len(valid_stubs)} functions, {len(failed_stubs)} failed")

    # Print usage
    log('INFO', f"Usage: {usage.cost_by_task()}")

    print_functions(valid_stubs)

    # Save to JSON harness
    meta = ProjectMetadata(
        objects=objects,
        functions=functions,
        endpoints=valid_stubs,
        usage=usage,
    )
    with open(args.output, 'w') as f:
        f.write(meta.model_dump_json(indent=2))


def register(subparsers):
    scan_parser = subparsers.add_parser('run')
    scan_parser.add_argument('--info', type=str, default='info.json', help='Path to the project info file')
    scan_parser.add_argument('--header', type=str, required=True)
    scan_parser.add_argument('--threads', type=int, default=1, help='Number of threads to use')
    scan_parser.add_argument('-o', '--output', type=str, required=True)
    scan_parser.set_defaults(func=main)
