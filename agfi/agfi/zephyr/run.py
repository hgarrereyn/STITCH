from typing import List, Tuple
import time

from .data import ObjectInfo, FunctionInfo, FunctionImplError, ProjectSynthMetadata, FunctionImpl

from ..data.metadata import UsageTracker
from ..data.project_info import ProjectInfo
from .data import ProjectMetadata

from .scan_objects import scan_objects
from .scan_functions import scan_functions, scan_function_names
from .synth_functions import incremental_synthesis_loop
from .data_classifier import classify_data
from .fill import run_fill_batch, analyze_blocking_missing_types
from .validation import run_validate_object
from .build_generators import build_generator

from .util import parallel_run, parallel_run_raw, log

SYNTH_RETRIES = 3


def process_header_functions(header_path: str, objects: List[ObjectInfo], info: ProjectInfo, threads: int, usage: UsageTracker) -> Tuple[List[FunctionInfo], List[FunctionImpl], List[FunctionImplError]]:
    code = open(header_path).read()
    
    log('INFO', f"Scanning header {header_path} for function names")
    function_names = scan_function_names(code, usage)
    log('SCAN', f"Found {len(function_names)} function names in {header_path}")

    log('INFO', f"Scanning header {header_path} for functions")
    functions = scan_functions(code, function_names, usage)
    log('SCAN', f"Found {len(functions)} functions in {header_path}")

    log('INFO', f"Synthesizing functions in {header_path}")
    valid_stubs, failed_stubs = incremental_synthesis_loop(objects, functions, usage, threads, info)
    log('SYNTH', f"Synthesized {len(valid_stubs)} functions, {len(failed_stubs)} failed in {header_path}")

    return functions, valid_stubs, failed_stubs


def main(args):
    info = ProjectInfo.model_validate_json(open(args.info).read())

    merged_header = ''
    for header in args.headers:
        merged_header += f'<CODE FILE="{header}">\n'
        merged_header += open(header).read()
        merged_header += '</CODE>\n'

    usage = UsageTracker()

    start_time = time.time()

    objects = []

    if len(args.headers) <= 3:
        # Scan for objects in the combined header
        log('INFO', f"Scanning combined header(s) for objects")
        s_objects = time.time()
        objects = scan_objects(merged_header, usage)
        t_objects = time.time() - s_objects
        log('SCAN', f"Found {len(objects)} objects")

    else:
        # Scan for objects in each header individually
        log('INFO', f"Scanning header(s) for objects")
        s_objects = time.time()
        objects = parallel_run(args.threads, scan_objects, [(open(header).read(), usage) for header in args.headers])
        t_objects = time.time() - s_objects
        log('SCAN', f"Found {len(objects)} objects")

    log('INFO', f"Validating {len(objects)} objects")
    s_validate_objects = time.time()
    objects: List[ObjectInfo] = parallel_run(args.threads, run_validate_object, [(info, o) for o in objects])
    t_validate_objects = time.time() - s_validate_objects
    log('SCAN', f"Validated {len(objects)} objects")

    functions = []
    valid_stubs = []
    failed_stubs = []

    t_functions = 0
    t_synthesis = 0

    if len(args.headers) <= 3:
        # For a few headers, we run on the merged header for better context preservation
        log('INFO', 'Small project -- using merged header')

        s_functions = time.time()
        log('INFO', 'Scanning merged header for function names')
        function_names = scan_function_names(merged_header, usage)
        log('SCAN', f"Found {len(function_names)} function names in merged header")

        log('INFO', 'Scanning merged header for functions')
        functions = scan_functions(merged_header, function_names, usage)
        log('SCAN', f"Found {len(functions)} functions in merged header")
        t_functions = time.time() - s_functions

        log('INFO', 'Synthesizing functions')
        s_synthesis = time.time()
        valid_stubs, failed_stubs = incremental_synthesis_loop(objects, functions, usage, args.threads, info)
        log('SYNTH', f"Synthesized {len(valid_stubs)} functions, {len(failed_stubs)} failed")
        t_synthesis = time.time() - s_synthesis
    else:
        # For many headers, we perform independent scans/synthesis to avoid context explosion
        log('INFO', 'Large project -- performing independent scans/synthesis')

        # Scan for functions in each header individually
        log('INFO', f"Scanning header(s) for functions")
        s_synthesis = time.time()
        res = parallel_run_raw(args.threads, process_header_functions, [(header_path, objects, info, args.threads, usage) for header_path in args.headers])
        for p_functions, p_valid_stubs, p_failed_stubs in res:
            functions.extend(p_functions)
            valid_stubs.extend(p_valid_stubs)
            failed_stubs.extend(p_failed_stubs)
        t_synthesis = time.time() - s_synthesis
        log('SCAN', f"Found {len(functions)} functions total")
        log('SYNTH', f"Synthesized {len(valid_stubs)} functions, {len(failed_stubs)} failed")


    meta = ProjectMetadata(
        objects=objects,
        functions=functions,
        endpoints=valid_stubs,
        usage=usage,
    )

    synth_meta = ProjectSynthMetadata(
        endpoint_failures=failed_stubs,
    )

    # If we have too many endpoints, use a more restricted context for filling
    # Otherwise, we can use the full context
    selective_context = len(meta.endpoints) > 300 or len(meta.objects) > 300

    log('INFO', f"Constructability analysis")
    target_types = analyze_blocking_missing_types(valid_stubs, objects, max_depth=6)

    t_fill = 0
    if len(target_types) > 0:
        log('INFO', f"Filling {len(target_types)} types")
        s_fill = time.time()
        valid_stubs, failed_stubs = run_fill_batch(info, meta, target_types, usage, args.threads, selective_context)
        meta.endpoints.extend(valid_stubs)
        synth_meta.endpoint_failures.extend(failed_stubs)
        log('FILL', f"Filled {len(valid_stubs)} functions, {len(failed_stubs)} failed")
        t_fill = time.time() - s_fill
    else:
        log('FILL', "No types to fill")

    # Perform per-endpoint classification
    log('INFO', f"Classifying data")
    s_classify = time.time()
    data_types, classifiers = classify_data(meta.objects, meta.functions, meta.endpoints, usage, selective_context)
    t_classify = time.time() - s_classify
    meta.data_types = data_types
    meta.endpoint_classifiers = classifiers
    log('CLASSIFY', f"Classified {len(data_types)} data types, {len(classifiers)} classifiers")

    # Build generators
    log('INFO', f"Building generators")
    s_build_generators = time.time()
    generators = parallel_run(args.threads, build_generator, [(d, meta.objects, meta.functions, meta.endpoints, meta.endpoint_classifiers, usage, selective_context) for d in meta.data_types])
    t_build_generators = time.time() - s_build_generators
    meta.generators = generators
    log('GENERATOR', f"Built {len(meta.generators)} generators")

    # Print usage
    log('INFO', f"Usage: {usage.cost_by_task()}")
    
    end_time = time.time()
    meta.metadata['total_time'] = end_time - start_time
    meta.metadata['scan_objects'] = t_objects
    meta.metadata['validate_objects'] = t_validate_objects
    meta.metadata['scan_functions'] = t_functions
    meta.metadata['synthesis'] = t_synthesis
    meta.metadata['fill'] = t_fill
    meta.metadata['classify'] = t_classify
    meta.metadata['build_generators'] = t_build_generators

    open(args.output, 'w').write(meta.model_dump_json(indent=2))
    open(args.output.replace('.json', '_failed.json'), 'w').write(synth_meta.model_dump_json(indent=2))


def register(subparsers):
    scan_parser = subparsers.add_parser('run')
    scan_parser.add_argument('--info', type=str, default='info.json', help='Path to the project info file')
    scan_parser.add_argument('--headers', type=str, required=True, nargs='+', help='List of header files to scan')
    scan_parser.add_argument('--threads', type=int, default=1, help='Number of threads to use')
    scan_parser.add_argument('-o', '--output', type=str, required=True)
    scan_parser.add_argument('--verbose', action='store_true', help='Verbose output')
    scan_parser.set_defaults(func=main)
