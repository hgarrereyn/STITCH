from __future__ import annotations

from typing import List, Tuple, Dict, Set, Optional, TYPE_CHECKING
import os
from argparse import ArgumentParser
from pathlib import Path
import json

from pydantic import BaseModel

from .data import FunctionImpl, FunctionInfo, ObjectInfo, ProjectMetadata, FunctionImplError
from ..data.metadata import UsageTracker
from ..data.project_info import ProjectInfo
from .validation import run_validate_stub
from .util import parallel_run as _parallel_run, call_llm
from .pretty import print_function_impl, pretty_print_error

import openai

if TYPE_CHECKING:
    from .ui import TokenProgress


PROMPT_FILL_BATCH = open(os.path.join(os.path.dirname(__file__), 'prompts/fill_batch.md')).read()
PROMPT_WITH_ERRORS = open(os.path.join(os.path.dirname(__file__), 'prompts/with_errors.md')).read()

MAX_RETRIES = 3
BATCH_SIZE = 20

MAX_ERROR_SIZE = 4000


def analyze_blocking_missing_types(endpoints: List[FunctionImpl], objects: List[ObjectInfo], max_depth: int = 6) -> Set[str]:
    """Return only the missing object types that block some endpoint input.

    A type is considered blocking if it is (a) not constructible within depth and (b) appears
    as an input type for at least one endpoint.
    """
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

    declared_types: Set[str] = {o.ptr_type for o in (objects or [])}
    all_types: Set[str] = declared_types | types_seen
    missing: Set[str] = set()
    for t in all_types:
        if not type_constructible(t, max_depth):
            missing.add(t)

    input_types: Set[str] = set()
    for in_list in endpoint_inputs.values():
        input_types.update(in_list)

    blocking: Set[str] = missing & input_types
    return blocking


class FillBatchResponse(BaseModel):
    endpoints: List[FunctionImpl]


def prompt_fill_ctx(metadata: ProjectMetadata, valid_endpoints: List[FunctionImpl], batch_types: List[str], selective_context: bool = False, with_errors: bool = False) -> list[dict]:
    ctx = [
        {
            "role": "developer",
            "content": (PROMPT_FILL_BATCH + '\n' + PROMPT_WITH_ERRORS) if with_errors else PROMPT_FILL_BATCH
        }
    ]    
    if selective_context:
        p_objects = [o.model_dump() for o in metadata.objects]

        relevant_endpoints = [e for e in metadata.endpoints + valid_endpoints if any(io.type in batch_types for io in e.inputs + e.outputs)]
        relevant_endpoint_names = [e.name for e in relevant_endpoints]

        relevant_functions = [f for f in metadata.functions if f.name in relevant_endpoint_names]

        p_endpoints = [e.model_dump() for e in relevant_endpoints]
        p_functions = [f.model_dump() for f in relevant_functions]

        ctx.append({
            "role": "user",
            "content": f'Objects:\n{json.dumps(p_objects)}\n\nFunctions:\n{json.dumps(p_functions)}\n\nEndpoints:\n{json.dumps(p_endpoints)}\n\nTarget types:\n{json.dumps(batch_types)}'
        })
    else:
        p_endpoints = [e.model_dump() for e in metadata.endpoints + valid_endpoints]
        p_objects = [o.model_dump() for o in metadata.objects]
        p_functions = [f.model_dump() for f in metadata.functions]
    
        ctx.append({
            "role": "user",
            "content": f'Objects:\n{json.dumps(p_objects)}\n\nFunctions:\n{json.dumps(p_functions)}\n\nEndpoints:\n{json.dumps(p_endpoints)}\n\nTarget types:\n{json.dumps(batch_types)}'
        })
    return ctx


def prompt_fill_batch(metadata: ProjectMetadata, valid_endpoints: List[FunctionImpl], batch_types: List[str], usage: UsageTracker, selective_context: bool = False, token_progress: Optional[TokenProgress] = None) -> List[FunctionImpl]:
    ctx = prompt_fill_ctx(metadata, valid_endpoints, batch_types, selective_context, with_errors=False)
    parsed: FillBatchResponse = call_llm(
        input=ctx,
        text_format=FillBatchResponse,
        usage=usage,
        task='fill-batch',
        reasoning={"effort": "low"},
        token_progress=token_progress,
    )
    return parsed.endpoints


def prompt_fill_batch_with_errors(metadata: ProjectMetadata, valid_endpoints: List[FunctionImpl], batch_types: List[str], failed_endpoints: List[FunctionImplError], usage: UsageTracker, selective_context: bool = False, token_progress: Optional[TokenProgress] = None) -> List[FunctionImpl]:
    ctx = prompt_fill_ctx(metadata, valid_endpoints, batch_types, selective_context, with_errors=True)

    truncated_failed_endpoints = []
    for e in failed_endpoints:
        if len(e.error) > MAX_ERROR_SIZE:
            truncated_failed_endpoints.append(FunctionImplError(stub=e.stub, type=e.type, error=e.error[:MAX_ERROR_SIZE]))
        else:
            truncated_failed_endpoints.append(e)

    p_failed_endpoints = [e.model_dump() for e in truncated_failed_endpoints]

    ctx.append({
        "role": "user",
        "content": f'Previous failure messages:\n{json.dumps(p_failed_endpoints)}'
    })

    parsed: FillBatchResponse = call_llm(
        input=ctx,
        text_format=FillBatchResponse,
        usage=usage,
        task='fill-batch',
        reasoning={"effort": "low"},
        token_progress=token_progress,
    )
    return parsed.endpoints


def run_fill_batch(
    info: ProjectInfo,
    metadata: ProjectMetadata,
    target_types,
    usage: UsageTracker,
    threads: int,
    selective_context: bool = False,
    verbose: bool = False,
    token_progress: Optional[TokenProgress] = None,
) -> List[FunctionImpl]:
    """Ask the LLM to synthesize constructor endpoints for target object types."""
    from . import ui

    if len(target_types) == 0:
        return [], []

    valid_endpoints = []
    total_failed_endpoints = []

    failed_endpoints: Dict[str, FunctionImplError] = {}

    remaining_types: Dict[str, int] = {t: 0 for t in target_types}

    step = 0
    while len(remaining_types) > 0:
        step += 1
        batch_size = min(BATCH_SIZE, len(remaining_types))

        ui.step_header(
            f"Fill step {step}  ·  "
            f"[yellow]{len(remaining_types)}[/yellow] types remaining"
        )

        batch_types = [t for t in remaining_types][:BATCH_SIZE]

        filtered_failed = [failed_endpoints[f] for f in batch_types if f in failed_endpoints]

        stubs = None
        tp = token_progress or ui.TokenProgress()
        with ui.spinner(f"Querying LLM (batch of {batch_size})", token_progress=tp):
            if len(filtered_failed) > 0:
                try:
                    stubs = prompt_fill_batch_with_errors(metadata, valid_endpoints, batch_types, filtered_failed, usage, selective_context, token_progress=tp)
                except openai.BadRequestError:
                    stubs = None
            else:
                try:
                    stubs = prompt_fill_batch(metadata, valid_endpoints, batch_types, usage, selective_context, token_progress=tp)
                except openai.BadRequestError:
                    stubs = None

        if stubs is None:
            ui.error("Context too large — skipping batch")
            for t in batch_types:
                if t in remaining_types:
                    remaining_types[t] += 1
                    if remaining_types[t] > MAX_RETRIES:
                        remaining_types.pop(t)
            continue

        results = ui.parallel_run(
            threads, run_validate_stub,
            [(info, s, metadata.objects) for s in stubs],
            item_labels=[s.name for s in stubs],
            ok=lambda r: isinstance(r[0].result, FunctionImpl),
        )

        n_valid = 0
        n_failed = 0

        for r in results:
            if isinstance(r.result, FunctionImpl):
                stub: FunctionImpl = r.result

                if verbose:
                    print_function_impl(stub)

                if stub.name in remaining_types:
                    valid_endpoints.append(stub)
                    remaining_types.pop(stub.name)
                    n_valid += 1
                else:
                    continue
            else:
                err: FunctionImplError = r.result

                if verbose:
                    pretty_print_error(err)

                if err.stub.name in remaining_types:
                    failed_endpoints[err.stub.name] = err
                    remaining_types[err.stub.name] += 1
                    if remaining_types[err.stub.name] > MAX_RETRIES:
                        remaining_types.pop(err.stub.name)
                        total_failed_endpoints.append(err)
                    n_failed += 1
                else:
                    continue

        parts = []
        if n_valid:
            parts.append(f"[green]{n_valid} valid[/green]")
        if n_failed:
            parts.append(f"[red]{n_failed} failed[/red]")
        if parts:
            ui.success(" · ".join(parts))

    return valid_endpoints, total_failed_endpoints


def main(args):
    from . import ui

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
