from __future__ import annotations

import json
from pydantic import BaseModel, Field
import os
from typing import List, Optional, Tuple, Dict, TYPE_CHECKING

from ..data.project_info import ProjectInfo

from .data import FunctionImpl, FunctionImplError, ObjectInfo, FunctionInfo, UnharnessableFunction
from .pretty import print_function_impl, pretty_print_error

from ..data.metadata import UsageTracker

from .validation import run_validate_stub

from .util import parallel_run as _parallel_run, call_llm

if TYPE_CHECKING:
    from .ui import TokenProgress


RETRIES = 3

MAX_ERROR_SIZE = 4000

PROMPT_SYNTH_FUNCTIONS = open(os.path.join(os.path.dirname(__file__), 'prompts/synth_functions.md')).read()
PROMPT_WITH_ERRORS = open(os.path.join(os.path.dirname(__file__), 'prompts/with_errors.md')).read()


class SynthResponse(BaseModel):
    functions: List[FunctionImpl] = Field(description="The list of function implementations")
    unharnessable: List[UnharnessableFunction] = Field(
        default_factory=list,
        description="Functions that cannot be safely or meaningfully harnessed, with reasons",
    )


def synth_functions(
    objects: List[ObjectInfo],
    functions: List[FunctionInfo],
    target_functions: List[str],
    usage: UsageTracker,
    token_progress: Optional[TokenProgress] = None,
) -> Tuple[List[FunctionImpl], List[UnharnessableFunction]]:
    p_objects = [o.model_dump() for o in objects],
    p_functions = [f.model_dump() for f in functions]
    p_target_functions = target_functions

    parsed: SynthResponse = call_llm(
        input=[
            {"role": "developer", "content": PROMPT_SYNTH_FUNCTIONS},
            {"role": "user", "content": f'Objects:\n{json.dumps(p_objects)}\n\nFunctions:\n{json.dumps(p_functions)}\n\nTarget functions:\n{json.dumps(p_target_functions)}'},
        ],
        text_format=SynthResponse,
        usage=usage,
        task='synth-functions',
        reasoning={"effort": "low"},
        token_progress=token_progress,
    )
    return parsed.functions, parsed.unharnessable


def synth_functions_with_errors(
    objects: List[ObjectInfo],
    functions: List[FunctionInfo],
    target_functions: List[str],
    usage: UsageTracker,
    existing_stubs: List[FunctionImpl],
    failed_stubs: List[FunctionImplError],
    token_progress: Optional[TokenProgress] = None,
) -> Tuple[List[FunctionImpl], List[UnharnessableFunction]]:
    p_objects = [o.model_dump() for o in objects],
    p_functions = [f.model_dump() for f in functions]
    p_target_functions = target_functions
    p_existing_stubs = [s.model_dump() for s in existing_stubs]

    truncated_failed_stubs = []
    for fs in failed_stubs:
        if len(fs.error) > MAX_ERROR_SIZE:
            truncated_failed_stubs.append(FunctionImplError(stub=fs.stub, type=fs.type, error=fs.error[:MAX_ERROR_SIZE]))
        else:
            truncated_failed_stubs.append(fs)

    p_failed_stubs = [s.model_dump() for s in truncated_failed_stubs]

    parsed: SynthResponse = call_llm(
        input=[
            {"role": "developer", "content": PROMPT_SYNTH_FUNCTIONS + '\n' + PROMPT_WITH_ERRORS},
            {"role": "user", "content": f'Objects:\n{json.dumps(p_objects)}\n\nFunctions:\n{json.dumps(p_functions)}\n\nTarget functions:\n{json.dumps(p_target_functions)}'},
            {"role": "user", "content": f'Existing stubs:\n{json.dumps(p_existing_stubs)}\n\nFailed stubs:\n{json.dumps(p_failed_stubs)}'},
        ],
        text_format=SynthResponse,
        usage=usage,
        task='synth-functions',
        reasoning={"effort": "low"},
        token_progress=token_progress,
    )
    return parsed.functions, parsed.unharnessable


def incremental_synthesis_loop(
    objects: List[ObjectInfo],
    functions: List[FunctionInfo],
    usage: UsageTracker,
    threads: int,
    info: ProjectInfo,
    retry_count: int = 2,
    max_batch_size: int = 20,
    show_progress: bool = True,
    verbose: bool = False,
    token_progress: Optional[TokenProgress] = None,
) -> Tuple[List[FunctionImpl], List[FunctionImplError], List[UnharnessableFunction]]:
    from . import ui

    valid_functions: List[FunctionImpl] = []
    total_failed_functions: List[FunctionImplError] = []

    total_unharnessable: List[UnharnessableFunction] = []
    unharnessable_by_name: Dict[str, UnharnessableFunction] = {}

    failed_functions: Dict[str, FunctionImplError] = {}

    remaining_functions: Dict[str, int] = {f.name: 0 for f in functions}

    step = 0
    while len(remaining_functions) > 0:
        step += 1
        batch_size = min(max_batch_size, len(remaining_functions))

        if show_progress:
            ui.step_header(
                f"Synthesis step {step}  ·  "
                f"[cyan]{len(remaining_functions)}[/cyan] functions remaining"
            )

        target_functions = [f for f in remaining_functions][:max_batch_size]

        filtered_failed = [failed_functions[f] for f in target_functions if f in failed_functions]

        stubs = None
        newly_unharnessable: List[UnharnessableFunction] = []

        def _query_llm():
            nonlocal stubs, newly_unharnessable
            if len(valid_functions) > 0 or len(filtered_failed) > 0:
                stubs, newly_unharnessable = synth_functions_with_errors(
                    objects, functions, target_functions, usage,
                    valid_functions, filtered_failed,
                    token_progress=token_progress,
                )
            else:
                stubs, newly_unharnessable = synth_functions(
                    objects, functions, target_functions, usage,
                    token_progress=token_progress,
                )

        if show_progress:
            tp = token_progress or ui.TokenProgress()
            with ui.spinner(f"Querying LLM (batch of {batch_size})", token_progress=tp):
                _query_llm()
        else:
            _query_llm()

        n_unharnessable = 0
        for uh in newly_unharnessable:
            if uh.name in remaining_functions:
                unharnessable_by_name[uh.name] = uh
                total_unharnessable.append(uh)
                remaining_functions.pop(uh.name)
                n_unharnessable += 1

        if show_progress:
            results = ui.parallel_run(
                threads, run_validate_stub,
                [(info, s, objects) for s in stubs],
                item_labels=[s.name for s in stubs],
                ok=lambda r: isinstance(r[0].result, FunctionImpl),
            )
        else:
            results = _parallel_run(
                threads, run_validate_stub,
                [(info, s, objects) for s in stubs],
            )

        n_valid = 0
        n_failed = 0

        for r in results:
            if isinstance(r.result, FunctionImpl):
                stub: FunctionImpl = r.result

                if verbose and show_progress:
                    print_function_impl(stub)

                if stub.name in remaining_functions:
                    valid_functions.append(stub)
                    remaining_functions.pop(stub.name)
                    n_valid += 1
                else:
                    continue
            else:
                error: FunctionImplError = r.result

                if verbose and show_progress:
                    pretty_print_error(error)

                if error.stub.name in remaining_functions:
                    failed_functions[error.stub.name] = error
                    remaining_functions[error.stub.name] += 1
                    if remaining_functions[error.stub.name] > retry_count:
                        remaining_functions.pop(error.stub.name)
                        total_failed_functions.append(error)
                    n_failed += 1
                else:
                    continue

        if show_progress:
            parts = []
            if n_valid:
                parts.append(f"[green]{n_valid} valid[/green]")
            if n_failed:
                parts.append(f"[red]{n_failed} failed[/red]")
            if n_unharnessable:
                parts.append(f"[yellow]{n_unharnessable} unharnessable[/yellow]")
            if parts:
                ui.success(" · ".join(parts))

    return valid_functions, total_failed_functions, total_unharnessable


def main(args):
    objects = [ObjectInfo.model_validate(x) for x in json.load(open(args.objects))]
    functions = [FunctionInfo.model_validate(x) for x in json.load(open(args.functions))]
    target_functions = args.target_functions
    usage = UsageTracker()
    stubs, unharnessable = synth_functions(objects, functions, target_functions, usage)
    print(usage.cost_by_task())

    from .pretty import print_functions
    print_functions(stubs)

    if args.output:
        functions_json = [x.model_dump() for x in stubs]
        meta = {
            "functions": functions_json,
            "unharnessable": [x.model_dump() for x in unharnessable],
        }
        with open(args.output, 'w') as f:
            f.write(json.dumps(meta, indent=2))


def register(subparsers):
    scan_parser = subparsers.add_parser('synth-functions')
    scan_parser.add_argument('--objects', type=str, required=True)
    scan_parser.add_argument('--functions', type=str, required=True)
    scan_parser.add_argument('--target-functions', type=str, required=True, nargs='+')
    scan_parser.add_argument('-o', '--output', type=str, required=False)
    scan_parser.set_defaults(func=main)
