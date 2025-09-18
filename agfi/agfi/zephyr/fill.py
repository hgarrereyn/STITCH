from typing import List, Tuple, Dict, Set
import os
from argparse import ArgumentParser
from pathlib import Path
import json

from pydantic import BaseModel

from .data import FunctionImpl, FunctionInfo, ObjectInfo, ProjectMetadata, FunctionImplError
from ..data.metadata import UsageTracker, UsageTokens
from ..data.project_info import ProjectInfo
from .validation import run_validate_stub
from .util import parallel_run, log
from ..utils.colors import Colors
from .pretty import print_function_impl, pretty_print_error

from openai import OpenAI


PROMPT_FILL_BATCH = open(os.path.join(os.path.dirname(__file__), 'prompts/fill_batch.md')).read()
PROMPT_WITH_ERRORS = open(os.path.join(os.path.dirname(__file__), 'prompts/with_errors.md')).read()

MAX_RETRIES = 3
BATCH_SIZE = 20


def analyze_blocking_missing_types(endpoints: List[FunctionImpl], objects: List[ObjectInfo], max_depth: int = 6) -> Set[str]:
    """Return only the missing object types that block some endpoint input.

    A type is considered blocking if it is (a) not constructible within depth and (b) appears
    as an input type for at least one endpoint.
    """
    # Normalize inputs/outputs and producers
    endpoint_inputs: Dict[str, List[str]] = {}
    endpoint_outputs: Dict[str, List[str]] = {}
    types_seen: Set[str] = set()

    for ep in endpoints or []:
        in_types: List[str] = [io.type for io in (ep.inputs or []) if io.type]
        out_types: List[str] = [io.type for io in (ep.outputs or []) if io.type]
        for t in in_types:
            types_seen.add(t)
        for t in out_types:
            types_seen.add(t)
        endpoint_inputs[ep.name] = in_types
        endpoint_outputs[ep.name] = out_types

    producers: Dict[str, List[str]] = {}
    for ep_name, outs in endpoint_outputs.items():
        for t in outs:
            producers.setdefault(t, []).append(ep_name)

    memo: Dict[Tuple[str, int], bool] = {}
    visiting: Set[Tuple[str, int]] = set()

    def type_constructible(tname: str, depth: int) -> bool:
        if depth == 0:
            return False
        key = (tname, depth)
        if key in memo:
            return memo[key]
        if key in visiting:
            memo[key] = False
            return False
        visiting.add(key)

        ok = False
        for ep_name in producers.get(tname, []):
            inputs_ok = True
            for it in endpoint_inputs.get(ep_name, []):
                if not type_constructible(it, depth - 1):
                    inputs_ok = False
                    break
            if inputs_ok:
                ok = True
                break

        visiting.remove(key)
        memo[key] = ok
        return ok

    # Compute missing types across seen types and declared object types
    declared_types: Set[str] = {o.ptr_type for o in (objects or [])}
    all_types: Set[str] = declared_types | types_seen
    missing: Set[str] = set()
    for t in all_types:
        if not type_constructible(t, max_depth):
            missing.add(t)

    # Only return those missing types that are used as inputs to at least one endpoint
    input_types: Set[str] = set()
    for in_list in endpoint_inputs.values():
        input_types.update(in_list)

    blocking: Set[str] = missing & input_types
    return blocking


class FillBatchResponse(BaseModel):
    endpoints: List[FunctionImpl]


def prompt_fill_batch(metadata: ProjectMetadata, valid_endpoints: List[FunctionImpl], batch_types: List[str], usage: UsageTracker) -> List[FunctionImpl]:
    client = OpenAI()
    
    p_endpoints = [e.model_dump() for e in metadata.endpoints + valid_endpoints]
    p_objects = [o.model_dump() for o in metadata.objects]
    p_functions = [f.model_dump() for f in metadata.functions]
    p_target_types = batch_types
    
    res = client.responses.parse(
        model='gpt-5',
        reasoning={ "effort": "low" },
        input=[
            {
                "role": "developer",
                "content": PROMPT_FILL_BATCH
            },
            {
                "role": "user",
                "content": f'Objects:\n{json.dumps(p_objects)}\n\nFunctions:\n{json.dumps(p_functions)}\n\nEndpoints:\n{json.dumps(p_endpoints)}\n\nTarget types:\n{json.dumps(p_target_types)}'
            },
        ],
        text_format=FillBatchResponse
    )
    usage.record_model('fill-batch', 'gpt-5', UsageTokens.from_openai(res.usage))
    return res.output_parsed.endpoints


def prompt_fill_batch_with_errors(metadata: ProjectMetadata, valid_endpoints: List[FunctionImpl], batch_types: List[str], failed_endpoints: List[FunctionImplError], usage: UsageTracker) -> List[FunctionImpl]:
    client = OpenAI()
    
    p_endpoints = [e.model_dump() for e in metadata.endpoints + valid_endpoints]
    p_objects = [o.model_dump() for o in metadata.objects]
    p_functions = [f.model_dump() for f in metadata.functions]
    p_target_types = batch_types
    p_failed_endpoints = [e.model_dump() for e in failed_endpoints]

    res = client.responses.parse(
        model='gpt-5',
        reasoning={ "effort": "low" },
        input=[
            {
                "role": "developer",
                "content": PROMPT_FILL_BATCH + '\n' + PROMPT_WITH_ERRORS
            },
            {
                "role": "user",
                "content": f'Objects:\n{json.dumps(p_objects)}\n\nFunctions:\n{json.dumps(p_functions)}\n\nEndpoints:\n{json.dumps(p_endpoints)}\n\nTarget types:\n{json.dumps(p_target_types)}'
            },
            {
                "role": "user",
                "content": f'Previous failure messages:\n{json.dumps(p_failed_endpoints)}'
            },
        ],
        text_format=FillBatchResponse
    )
    usage.record_model('fill-batch', 'gpt-5', UsageTokens.from_openai(res.usage))
    return res.output_parsed.endpoints


def run_fill_batch(info: ProjectInfo, metadata: ProjectMetadata, target_types: List[str], usage: UsageTracker, threads: int) -> List[FunctionImpl]:
    """Ask the LLM to synthesize constructor endpoints for target object types; return new validated endpoints."""
    if len(target_types) == 0:
        return []

    # Keep track of valid and failed endpoints
    valid_endpoints = []
    total_failed_endpoints = []

    # Failed endpoints by name (we only keep the latest error for each endpoint)
    failed_endpoints: Dict[str, FunctionImplError] = {}

    # Types left to synthesize and how many times we've tried to synthesize them
    remaining_types: Dict[str, int] = {t: 0 for t in target_types}

    step = 0
    while len(remaining_types) > 0:
        step += 1
        log('INFO', f"Fill step {step} -- {len(remaining_types)} types remaining")

        # Prepare the next batch of types
        target_types = [t for t in remaining_types][:BATCH_SIZE]

        # Extract the failed functions relevant for this batch
        filtered_failed = [failed_endpoints[f] for f in target_types if f in failed_endpoints]

        # Query the LLM
        stubs = None
        if len(filtered_failed) > 0:
            stubs = prompt_fill_batch_with_errors(metadata, valid_endpoints, target_types, filtered_failed, usage)
        else:
            stubs = prompt_fill_batch(metadata, valid_endpoints, target_types, usage)

        res: List[FunctionImpl] = stubs
        
        # Validate the endpoints in parallel
        results = parallel_run(threads, run_validate_stub, [(info, s, metadata.objects) for s in res])

        for r in results:
            if isinstance(r.result, FunctionImpl):
                stub: FunctionImpl = r.result

                print_function_impl(stub)

                if stub.name in remaining_types:
                    log('VALID', f"Valid endpoint for {Colors.YELLOW}{stub.name}{Colors.END}")
                    valid_endpoints.append(stub)
                    remaining_types.pop(stub.name)
                else:
                    log('VALID', f"Extraneous stub for {Colors.YELLOW}{stub.name}{Colors.END} (not in remaining types)")
                    continue
            else:
                error: FunctionImplError = r.result
                pretty_print_error(error)

                if error.stub.name in remaining_types:
                    log('ERROR', f"Invalid endpoint for {Colors.YELLOW}{error.stub.name}{Colors.END}")
                    failed_endpoints[error.stub.name] = error
                    remaining_types[error.stub.name] += 1 # Increment the retry count
                    if remaining_types[error.stub.name] > MAX_RETRIES:
                        log('ERROR', f"Failed to synthesize endpoint for {Colors.YELLOW}{error.stub.name}{Colors.END} after {MAX_RETRIES} attempts")
                        remaining_types.pop(error.stub.name)
                        total_failed_endpoints.append(error)
                else:
                    log('ERROR', f"Extraneous stub for {Colors.YELLOW}{error.stub.name}{Colors.END} (not in remaining functions)")
                    continue


    return valid_endpoints, total_failed_endpoints


def main(args):
    info = ProjectInfo.model_validate_json(open(args.info).read())
    metadata = ProjectMetadata.model_validate_json(open(args.metadata).read())
    target_types = analyze_blocking_missing_types(metadata.endpoints, metadata.objects, max_depth=6)
    valid_endpoints, failed_endpoints = run_fill_batch(info, metadata, target_types, metadata.usage, args.threads)

    print(metadata.usage.cost_by_task())

    metadata.endpoints.extend(valid_endpoints)

    open(args.output, 'w').write(metadata.model_dump_json(indent=2))


def register(subparser: ArgumentParser):
    parser = subparser.add_parser('fill', help='Fill missing constructor endpoints')
    parser.add_argument('--info', type=str, default='info.json', help='The project info file')
    parser.add_argument('--metadata', type=str, required=True, help='The project metadata file')
    parser.add_argument('--output', type=str, required=True, help='The output file')
    parser.add_argument('--threads', type=int, default=1, help='The number of threads to use')
    parser.set_defaults(func=main)
