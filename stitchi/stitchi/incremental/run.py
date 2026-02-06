from typing import List, Any, Tuple
import difflib
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pydantic import BaseModel, Field
from enum import Enum
from datetime import datetime
import time

from ..utils.colors import Colors

from ..data.project_info import ProjectInfo
from ..data.metadata import UsageTracker

from .data import FunctionEntryBatch, ProjectMetadata, FailedFunctionImpl, FunctionImpl, ObjectStub, FunctionStub, SynthesisEvent
from .scan_objects import scan_objects, run_validate_object
from .scan_functions import scan_functions, run_validate_function, scan_function_info_batch
from .synth import run_synth_batch, validate_impl_build, run_synth_batch_with_errors
from .fill import analyze_blocking_missing_types, run_fill_batch
from .polish import run_polish_batch
from .generate_hints import run_generate_hints_batch


def smash_fn(args) -> Any:
    fn = args[0]
    return fn(*args[1])


def parallel_run(threads: int, func: Any, inputs: List[Any]) -> List[Any]:
    with ThreadPoolExecutor(max_workers=threads) as executor:
        res = list(executor.map(smash_fn, [(func, i) for i in inputs]))

    # Flatten the list
    return [item for sublist in res for item in sublist]


def log(mode: str, msg: str):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] [{Colors.BOLD}{Colors.GREEN}{mode}{Colors.END}] {msg}")


def log_events_summary(events: List[SynthesisEvent]):
    """Log a summary of synthesis events"""
    if not events:
        log('EVENTS', "No events recorded")
        return
    
    # Count events by type
    event_counts = {}
    for event in events:
        event_counts[event.event_type] = event_counts.get(event.event_type, 0) + 1
    
    log('EVENTS', f"Total events recorded: {len(events)}")
    for event_type, count in event_counts.items():
        log('EVENTS', f"  {event_type}: {count}")
    
    # Show failed functions
    failed_functions = set()
    for event in events:
        if event.event_type in ['synthesis_failure', 'validation_failure']:
            failed_functions.add(event.function_name)
    
    if failed_functions:
        log('EVENTS', f"Functions that failed: {', '.join(sorted(failed_functions))}")


def export_events_to_file(events: List[SynthesisEvent], output_path: str):
    """Export events to a JSON file for analysis"""
    if not events:
        return
    
    import json
    from datetime import datetime
    
    # Convert events to dict format for JSON serialization
    events_data = []
    for event in events:
        event_dict = event.model_dump()
        # Convert datetime to string for JSON serialization
        if isinstance(event_dict['timestamp'], datetime):
            event_dict['timestamp'] = event_dict['timestamp'].isoformat()
        events_data.append(event_dict)
    
    with open(output_path, 'w') as f:
        json.dump(events_data, f, indent=2)
    
    log('EVENTS', f"Exported {len(events)} events to {output_path}")


def synthesis_fix_loop(info: ProjectInfo, objects: List[ObjectStub], functions: List[FunctionStub], endpoints: List[FunctionImpl], usage: UsageTracker, model: str, threads: int, events: List[SynthesisEvent]) -> Tuple[List[FunctionImpl], List[FailedFunctionImpl]]:
    remaining_endpoints = list(endpoints)
    validated_endpoints = []

    # While there are validation errors, keep regenerating the endpoints
    MAX_REGEN_ATTEMPTS = 3
    for regen_attempt in range(MAX_REGEN_ATTEMPTS):
        log(f'REGEN-{regen_attempt}', f"Validating {len(remaining_endpoints)} endpoints")
        endpoints = parallel_run(threads, validate_impl_build, [(info, e, objects, events) for e in remaining_endpoints])

        failed_endpoints = [e for e in endpoints if not e.valid]
        valid_endpoints = [e for e in endpoints if e.valid]

        log('VALID', f"Validated {len(valid_endpoints)} endpoints, {len(failed_endpoints)} invalid")

        validated_endpoints.extend(valid_endpoints)

        # Print the invalid endpoints
        for e in failed_endpoints:
            print(f'{Colors.RED}Invalid endpoint for {Colors.YELLOW}{e.impl.name}{Colors.END}:')
            print(e.error)
            print('-' * 80)

        if len(failed_endpoints) > 0:
            if regen_attempt == MAX_REGEN_ATTEMPTS - 1:
                log('REGEN', f"Failed to regenerate {len(failed_endpoints)} endpoints after {MAX_REGEN_ATTEMPTS} attempts")
                failed_endpoints = [FailedFunctionImpl(name=e.impl.name, impl=e.impl, error=e.error) for e in failed_endpoints]
                break

            log(f'REGEN-{regen_attempt}', f"Regenerating {len(failed_endpoints)} invalid endpoints")
            MAX_BATCH_SIZE = 10

            batch_attempts = []
            batch_functions = []

            for i in range(0, len(failed_endpoints), MAX_BATCH_SIZE):
                batch = failed_endpoints[i:i+MAX_BATCH_SIZE]
                # lookup the corresponding function stubs
                function_batch = []
                for e in batch:
                    for f in functions:
                        if f.name == e.impl.name:
                            function_batch.append(f)
                            break

                batch_attempts.append(batch)
                batch_functions.append(function_batch)

            remaining_endpoints = parallel_run(threads, run_synth_batch_with_errors, [(model, batch_attempts, batch_functions, objects, usage, events) for batch_attempts, batch_functions in zip(batch_attempts, batch_functions)])
            log('SYNTH', f"Synthesized {len(remaining_endpoints)} endpoints")
        else:
            break

    validated_endpoints = [x.impl for x in validated_endpoints]
    return validated_endpoints, failed_endpoints


def main(args):
    info = ProjectInfo.model_validate_json(open(args.info).read())
    metadata = ProjectMetadata.model_validate_json(open(args.metadata).read()) if args.metadata else ProjectMetadata()

    headers = {h: open(h).read() for h in args.headers}

    start_time = time.time()

    if metadata.objects is None:
        log('INFO', f"Scanning {len(args.headers)} header files for objects")
        objects = parallel_run(args.threads, scan_objects, [(args.model, code, metadata.usage) for code in headers.values()])
        log('SCAN', f"Found {len(objects)} objects")

        objects = parallel_run(args.threads, run_validate_object, [(info, o) for o in objects])
        log('SCAN', f"Validated {len(objects)} objects")

        metadata.objects = objects

        if args.output:
            open(args.output, 'w').write(metadata.model_dump_json(indent=2))

    if metadata.functions is None:
        log('INFO', f"Scanning {len(args.headers)} header files for functions")
        function_entries = parallel_run(args.threads, scan_functions, [(args.model, name, code, metadata.usage) for (name, code) in headers.items()])
        log('SCAN', f"Found {len(function_entries)} potential functions")

        function_entries = parallel_run(args.threads, run_validate_function, [(info, e) for e in function_entries])
        log('SCAN', f"Validated {len(function_entries)} functions")

        # Split into batches where each batch contains functions from the same header, and the batch size is at most MAX_BATCH_SIZE
        MAX_BATCH_SIZE = 40
        by_header = defaultdict(list)
        for entry in function_entries:
            by_header[entry.header].append(entry)
        
        batches = []
        for header, entries in by_header.items():
            for i in range(0, len(entries), MAX_BATCH_SIZE):
                batches.append(FunctionEntryBatch(header=header, header_code=headers[header], entries=entries[i:i+MAX_BATCH_SIZE]))

        function_stubs = parallel_run(args.threads, scan_function_info_batch, [(args.model, batch, metadata.usage) for batch in batches])
        log('SCAN', f"Scanned {len(function_stubs)} functions")

        metadata.functions = function_stubs

        if args.output:
            open(args.output, 'w').write(metadata.model_dump_json(indent=2))

    # Initial synthesis attempt
    if metadata.endpoints is None:
        log('INFO', f"Synthesizing endpoints for {len(metadata.functions)} functions")
        MAX_BATCH_SIZE = 20
        batches = [metadata.functions[i:i+MAX_BATCH_SIZE] for i in range(0, len(metadata.functions), MAX_BATCH_SIZE)]
        endpoints = parallel_run(args.threads, run_synth_batch, [(args.model, batch, metadata.objects, metadata.usage, metadata.events) for batch in batches])
        metadata.endpoints = endpoints
        log('SYNTH', f"Synthesized {len(endpoints)} endpoints")
        log_events_summary(metadata.events)

        if args.output:
            open(args.output, 'w').write(metadata.model_dump_json(indent=2))

    # Fix synthesis errors
    if metadata.validated_endpoints is None:
        validated_endpoints, failed_endpoints = synthesis_fix_loop(info, metadata.objects, metadata.functions, metadata.endpoints, metadata.usage, args.model, args.threads, metadata.events) #

        metadata.validated_endpoints = validated_endpoints
        metadata.failed_endpoints = failed_endpoints
        log_events_summary(metadata.events)

        if args.output:
            open(args.output, 'w').write(metadata.model_dump_json(indent=2))

    # Fill stage: identify missing constructor object types and synthesize constructor endpoints in batches
    if metadata.validated_endpoints:
        MAX_FILL_PASSES = 3
        FILL_BATCH_SIZE = 20
        for fill_iter in range(MAX_FILL_PASSES):
            missing_types = analyze_blocking_missing_types(metadata.validated_endpoints, metadata.objects, max_depth=6)
            # Only attempt to fill object types that are in our known objects set and actually block endpoints
            known_types = {o.type for o in metadata.objects or []} | {'void *'}
            target_types = [t for t in missing_types if t in known_types]
            if not target_types:
                break
            log('FILL', f"Missing constructors for {len(target_types)} types; attempting to synthesize...")

            aggregated_new_endpoints: List[FunctionImpl] = []
            for i in range(0, len(target_types), FILL_BATCH_SIZE):
                batch_types = target_types[i:i+FILL_BATCH_SIZE]
                new_eps = run_fill_batch(args.model, info, metadata.functions, metadata.objects, metadata.validated_endpoints, batch_types, metadata.usage, metadata.events)
                if new_eps:
                    aggregated_new_endpoints.extend(new_eps)
                    # Merge and keep unique by name (new overrides old)
                    by_name = {e.name: e for e in metadata.validated_endpoints}
                    for e in new_eps:
                        by_name[e.name] = e
                    metadata.validated_endpoints = list(by_name.values())

            if not aggregated_new_endpoints:
                log('FILL', 'No constructor endpoints synthesized; stopping fill.')
                break

            log('FILL', f"Added {len(aggregated_new_endpoints)} constructor endpoints.")
            if args.output:
                open(args.output, 'w').write(metadata.model_dump_json(indent=2))

    # Polish stage: run up to 5 passes or until no more changes, in batches of 20 endpoints
    MAX_POLISH_PASSES = 5
    POLISH_BATCH_SIZE = 20
    if metadata.validated_endpoints:
        for polish_iter in range(MAX_POLISH_PASSES):
            log('POLISH', f"Running polish pass {polish_iter + 1}/{MAX_POLISH_PASSES}")
            total_changes: List[SynthesisEvent] = []
            aggregated_changes = []
            updated_endpoints = metadata.validated_endpoints

            for i in range(0, len(metadata.validated_endpoints), POLISH_BATCH_SIZE):
                focus_batch = metadata.validated_endpoints[i:i+POLISH_BATCH_SIZE]
                changes, updated_endpoints = run_polish_batch(args.model, info, metadata.functions, metadata.objects, updated_endpoints, focus_batch, metadata.usage, metadata.events)
                if changes:
                    # Print diffs for this batch
                    old_by_name = {e.name: e for e in metadata.validated_endpoints}
                    for ch in changes:
                        old_impl = old_by_name.get(ch.name)
                        old_code = (old_impl.code if old_impl else '')
                        new_code = ch.impl.code
                        print(f"[{Colors.BOLD}{Colors.GREEN}POLISH{Colors.END}] {Colors.YELLOW}{ch.name}{Colors.END}: {ch.description}")
                        print('--- old')
                        print('+++ new')
                        diff_lines = difflib.unified_diff(
                            old_code.splitlines(),
                            new_code.splitlines(),
                            lineterm='',
                            n=3,
                        )
                        for line in diff_lines:
                            print(line)
                        print('-' * 80)
                    aggregated_changes.extend(changes)

            if not aggregated_changes:
                log('POLISH', "No changes proposed in any batch; stopping polish.")
                break

            metadata.validated_endpoints = updated_endpoints

            # Persist changes metadata
            metadata.polish_changes = (metadata.polish_changes or []) + aggregated_changes
            log('POLISH', f"Applied {len(aggregated_changes)} changes; {len(metadata.validated_endpoints)} endpoints validated.")

            if args.output:
                open(args.output, 'w').write(metadata.model_dump_json(indent=2))

    # Generate hints for endpoints
    if metadata.endpoint_hints is None and metadata.validated_endpoints:
        log('INFO', f"Generating hints for {len(metadata.validated_endpoints)} endpoints")
        MAX_BATCH_SIZE = 20
        batches = [metadata.validated_endpoints[i:i+MAX_BATCH_SIZE] for i in range(0, len(metadata.validated_endpoints), MAX_BATCH_SIZE)]
        endpoint_hints = parallel_run(args.threads, run_generate_hints_batch, [(args.model, batch, metadata.objects, metadata.usage) for batch in batches])
        metadata.endpoint_hints = endpoint_hints
        log('HINT', f"Generated hints for {len(metadata.endpoint_hints)} endpoints")

        if args.output:
            open(args.output, 'w').write(metadata.model_dump_json(indent=2))


    end_time = time.time()
    log('INFO', f"Total time: {end_time - start_time} seconds")
    log_events_summary(metadata.events)

    metadata.metadata['total_time'] = end_time - start_time

    if args.strip:
        metadata.events = []
        metadata.polish_changes = []

    if args.output:
        open(args.output, 'w').write(metadata.model_dump_json(indent=2))
    
    if args.export_events:
        export_events_to_file(metadata.events, args.export_events)


def register(subparsers):
    parser = subparsers.add_parser('run', help='Run the incremental fuzzer')
    parser.add_argument('--info', type=str, default='info.json', help='Path to the project info file')
    parser.add_argument('--headers', type=str, nargs='+', required=True, help='List of header files to scan')
    parser.add_argument('--model', type=str, default='gpt-4.1', help='Model to use for scanning')
    parser.add_argument('--metadata', type=str, default=None, help='Path to the metadata file')
    parser.add_argument('--output', type=str, default=None, help='Path to the output file')
    parser.add_argument('--threads', type=int, default=1, help='Number of threads to use')
    parser.add_argument('--export-events', type=str, default=None, help='Path to export events JSON file')
    parser.add_argument('--strip', action='store_true', help='Strip extra metadata from the output file')
    parser.set_defaults(func=main)
