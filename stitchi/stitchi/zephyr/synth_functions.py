from cmd import PROMPT
import json
from pydantic import BaseModel, Field
import os
from typing import List, Optional, Tuple, Dict

from ..data.project_info import ProjectInfo

from .data import FunctionImpl, FunctionImplError, ObjectInfo, FunctionInfo, UnharnessableFunction
from .pretty import print_functions, print_function_impl, pretty_print_error

from ..utils.colors import Colors
from ..data.metadata import UsageTracker, UsageTokens

from .validation import run_validate_stub

from .util import parallel_run, log, get_model

from openai import OpenAI


RETRIES = 3

# max number of characters for an error message before it is truncated
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
) -> Tuple[List[FunctionImpl], List[UnharnessableFunction]]:
    client = OpenAI()
    model = get_model()

    p_objects = [o.model_dump() for o in objects],
    p_functions = [f.model_dump() for f in functions]
    p_target_functions = target_functions

    response = client.responses.parse(
        model=model,
        reasoning={ "effort": "low" },
        input=[
            {
                "role": "developer",
                "content": PROMPT_SYNTH_FUNCTIONS
            },
            {
                "role": "user",
                "content": f'Objects:\n{json.dumps(p_objects)}\n\nFunctions:\n{json.dumps(p_functions)}\n\nTarget functions:\n{json.dumps(p_target_functions)}'
            },
        ],
        text_format=SynthResponse
    )
    usage.record_model('synth-functions', model, UsageTokens.from_openai(response.usage))
    parsed = response.output_parsed

    return parsed.functions, parsed.unharnessable


def synth_functions_with_errors(
    objects: List[ObjectInfo],
    functions: List[FunctionInfo],
    target_functions: List[str],
    usage: UsageTracker,
    existing_stubs: List[FunctionImpl],
    failed_stubs: List[FunctionImplError],
) -> Tuple[List[FunctionImpl], List[UnharnessableFunction]]:
    client = OpenAI()
    model = get_model()

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

    response = client.responses.parse(
        model=model,
        reasoning={ "effort": "low" },
        input=[
            {
                "role": "developer",
                "content": PROMPT_SYNTH_FUNCTIONS + '\n' + PROMPT_WITH_ERRORS
            },
            {
                "role": "user",
                "content": f'Objects:\n{json.dumps(p_objects)}\n\nFunctions:\n{json.dumps(p_functions)}\n\nTarget functions:\n{json.dumps(p_target_functions)}'
            },
            {
                "role": "user",
                "content": f'Existing stubs:\n{json.dumps(p_existing_stubs)}\n\nFailed stubs:\n{json.dumps(p_failed_stubs)}'
            },
        ],
        text_format=SynthResponse
    )
    usage.record_model('synth-functions', model, UsageTokens.from_openai(response.usage))
    parsed = response.output_parsed

    return parsed.functions, parsed.unharnessable


def incremental_synthesis_loop(
    objects: List[ObjectInfo],
    functions: List[FunctionInfo],
    usage: UsageTracker,
    threads: int,
    info: ProjectInfo,
    retry_count: int = 2,
    max_batch_size: int = 20,
) -> Tuple[List[FunctionImpl], List[FunctionImplError], List[UnharnessableFunction]]:
    # Maintain a state of the current harness, including validated and failed functions.
    # We will attempt each function up to retry_count times.
    # Functions are queried in batches of max_batch_size.

    # The current state of the harness
    valid_functions: List[FunctionImpl] = []
    total_failed_functions: List[FunctionImplError] = []

    # Functions that have been explicitly marked as unharnessable by the model
    total_unharnessable: List[UnharnessableFunction] = []
    unharnessable_by_name: Dict[str, UnharnessableFunction] = {}

    # Failed functions by name (we only keep the latest error for each function)
    failed_functions: Dict[str, FunctionImplError] = {}

    # Functions left to synthesize and how many times we've tried to synthesize them
    remaining_functions: Dict[str, int] = {f.name: 0 for f in functions}

    step = 0
    while len(remaining_functions) > 0:
        step += 1
        log('INFO', f"Synthesis step {step} -- {len(remaining_functions)} functions remaining")

        # Prepare the next batch of functions
        target_functions = [f for f in remaining_functions][:max_batch_size]

        # Extract the failed functions relevant for this batch
        filtered_failed = [failed_functions[f] for f in target_functions if f in failed_functions]

        # Query the LLM
        stubs = None
        newly_unharnessable: List[UnharnessableFunction] = []
        if len(valid_functions) > 0 or len(filtered_failed) > 0:
            stubs, newly_unharnessable = synth_functions_with_errors(
                objects,
                functions,
                target_functions,
                usage,
                valid_functions,
                filtered_failed,
            )
        else:
            stubs, newly_unharnessable = synth_functions(
                objects,
                functions,
                target_functions,
                usage,
            )

        # Immediately record any functions the model has declared unharnessable
        for uh in newly_unharnessable:
            if uh.name in remaining_functions:
                log('INFO', f"Marking function as unharnessable: {Colors.YELLOW}{uh.name}{Colors.END} -- {uh.reason}")
                unharnessable_by_name[uh.name] = uh
                total_unharnessable.append(uh)
                remaining_functions.pop(uh.name)
            else:
                log('INFO', f"Extraneous unharnessable entry for {Colors.YELLOW}{uh.name}{Colors.END} (not in remaining functions)")

        # Validate the stubs in parallel
        results = parallel_run(threads, run_validate_stub, [(info, s, objects) for s in stubs])

        for r in results:
            if isinstance(r.result, FunctionImpl):
                stub: FunctionImpl = r.result

                print_function_impl(stub)

                if stub.name in remaining_functions:
                    log('VALID', f"Valid stub for {Colors.YELLOW}{stub.name}{Colors.END}")
                    valid_functions.append(stub)
                    remaining_functions.pop(stub.name)
                else:
                    log('VALID', f"Extraneous stub for {Colors.YELLOW}{stub.name}{Colors.END} (not in remaining functions)")
                    continue
            else:
                error: FunctionImplError = r.result
                pretty_print_error(error)

                if error.stub.name in remaining_functions:
                    log('ERROR', f"Invalid stub for {Colors.YELLOW}{error.stub.name}{Colors.END}")
                    failed_functions[error.stub.name] = error
                    remaining_functions[error.stub.name] += 1 # Increment the retry count
                    if remaining_functions[error.stub.name] > retry_count:
                        log('ERROR', f"Failed to synthesize stub for {Colors.YELLOW}{error.stub.name}{Colors.END} after {retry_count} attempts")
                        remaining_functions.pop(error.stub.name)
                        total_failed_functions.append(error)
                else:
                    log('ERROR', f"Extraneous stub for {Colors.YELLOW}{error.stub.name}{Colors.END} (not in remaining functions)")
                    continue

    return valid_functions, total_failed_functions, total_unharnessable


def main(args):
    objects = [ObjectInfo.model_validate(x) for x in json.load(open(args.objects))]
    functions = [FunctionInfo.model_validate(x) for x in json.load(open(args.functions))]
    target_functions = args.target_functions
    usage = UsageTracker()
    stubs, unharnessable = synth_functions(objects, functions, target_functions, usage)
    print(usage.cost_by_task())

    # Pretty print the functions with syntax highlighting
    print_functions(stubs)

    # Still save to JSON if output file is specified
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
