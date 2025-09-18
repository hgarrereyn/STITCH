from typing import List, Any
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import time

from .data import ObjectInfo, FunctionInfo, FunctionImplError, FunctionImpl, ProjectSynthMetadata
from .pretty import print_functions, pretty_print_error

from ..utils.colors import Colors
from ..data.metadata import UsageTracker, UsageTokens
from ..data.project_info import ProjectInfo
from .data import ProjectMetadata

from .scan_objects import scan_objects
from .scan_functions import scan_functions, scan_function_names
from .synth_functions import incremental_synthesis_loop
from .data_classifier import classify_data
from .fill import run_fill_batch, analyze_blocking_missing_types
from .validation import run_validate_object, run_validate_function_name, run_validate_stub
from .build_generators import build_generators

from .util import parallel_run, log

SYNTH_RETRIES = 3


def main(args):
    info = ProjectInfo.model_validate_json(open(args.info).read())

    code = ''
    for header in args.headers:
        code += f'<CODE FILE="{header}">\n'
        code += open(header).read()
        code += '</CODE>\n'

    usage = UsageTracker()

    start_time = time.time()

    log('INFO', f"Scanning header(s) for objects")
    s_objects = time.time()
    objects = scan_objects(code, usage)
    t_objects = time.time() - s_objects
    log('SCAN', f"Found {len(objects)} objects")

    log('INFO', f"Validating {len(objects)} objects")
    s_validate_objects = time.time()
    objects: List[ObjectInfo] = parallel_run(args.threads, run_validate_object, [(info, o) for o in objects])
    t_validate_objects = time.time() - s_validate_objects
    log('SCAN', f"Validated {len(objects)} objects")

    log('INFO', f"Scanning header(s) for function names")
    s_function_names = time.time()
    function_names = scan_function_names(code, usage)
    t_function_names = time.time() - s_function_names
    log('SCAN', f"Found {len(function_names)} function names")

    log('INFO', f"Validating {len(function_names)} function names")
    s_validate_function_names = time.time()
    function_names = parallel_run(args.threads, run_validate_function_name, [(info, f) for f in function_names])
    t_validate_function_names = time.time() - s_validate_function_names
    log('SCAN', f"Validated {len(function_names)} function names")

    log('INFO', f"Scanning header(s) for functions")
    s_functions = time.time()
    functions = scan_functions(code, function_names, usage)
    t_functions = time.time() - s_functions
    log('SCAN', f"Found {len(functions)} functions")

    s_synthesis = time.time()
    valid_stubs, failed_stubs = incremental_synthesis_loop(objects, functions, usage, args.threads, info)
    t_synthesis = time.time() - s_synthesis
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

    log('INFO', f"Constructability analysis")
    target_types = analyze_blocking_missing_types(valid_stubs, objects, max_depth=6)

    t_fill = 0
    if len(target_types) > 0:
        log('INFO', f"Filling {len(target_types)} types")
        s_fill = time.time()
        valid_stubs, failed_stubs = run_fill_batch(info, meta, target_types, usage, args.threads)
        meta.endpoints.extend(valid_stubs)
        synth_meta.endpoint_failures.extend(failed_stubs)
        log('FILL', f"Filled {len(valid_stubs)} functions, {len(failed_stubs)} failed")
        t_fill = time.time() - s_fill
    else:
        log('FILL', "No types to fill")

    # Perform per-endpoint classification
    log('INFO', f"Classifying data")
    s_classify = time.time()
    data_types, classifiers = classify_data(meta.objects, meta.functions, meta.endpoints, usage)
    t_classify = time.time() - s_classify
    meta.data_types = data_types
    meta.endpoint_classifiers = classifiers
    log('CLASSIFY', f"Classified {len(data_types)} data types, {len(classifiers)} classifiers")

    # Build generators
    log('INFO', f"Building generators")
    s_build_generators = time.time()
    generators = build_generators(meta.objects, meta.functions, meta.endpoints, meta.endpoint_classifiers, meta.data_types, usage)
    t_build_generators = time.time() - s_build_generators
    meta.generators = generators
    log('GENERATOR', f"Built {len(meta.generators)} generators")

    # Print usage
    log('INFO', f"Usage: {usage.cost_by_task()}")
    
    end_time = time.time()
    meta.metadata['total_time'] = end_time - start_time
    meta.metadata['scan_objects'] = t_objects
    meta.metadata['validate_objects'] = t_validate_objects
    meta.metadata['scan_function_names'] = t_function_names
    meta.metadata['validate_function_names'] = t_validate_function_names
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
    scan_parser.set_defaults(func=main)
