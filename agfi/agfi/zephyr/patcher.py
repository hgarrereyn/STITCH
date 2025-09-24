from argparse import ArgumentParser
import os
import difflib
from typing import List
from pydantic import BaseModel, Field
from openai import OpenAI
from ..data.metadata import UsageTracker, UsageTokens
from .data import FunctionImpl
import json
from ..data.project_info import ProjectInfo
from .data import ProjectMetadata
from .distill_agent import CrashAssessment
from .pretty import print_functions, pretty_print_code, print_function_impl
from .validation import run_validate_stub
from .util import parallel_run
from ..utils.colors import Colors


PROMPT_PATCHER = open(os.path.join(os.path.dirname(__file__), 'prompts/patcher.md')).read()

MAX_RETRIES = 3


class PatchInfo(BaseModel):
    original_stubs: List[FunctionImpl]
    modified_stubs: List[FunctionImpl]
    usage: UsageTracker


class PatcherResponse(BaseModel):
    stubs: List[FunctionImpl] = Field(description="The list of edited fuzzer stubs with new implementations")


def run_patcher_full_context(info: ProjectInfo, harness: ProjectMetadata, assessment: CrashAssessment, threads: int) -> PatchInfo:
    usage = UsageTracker()
    client = OpenAI()

    p_objects = [o.model_dump_json() for o in harness.objects]
    p_functions = [f.model_dump_json() for f in harness.functions]
    p_endpoints = [e.model_dump_json() for e in harness.endpoints]
    p_assessment = assessment.model_dump()
    
    ctx = [
        {
            "role": "developer",
            "content": PROMPT_PATCHER
        },
        {
            "role": "user",
            "content": f'Existing fuzzer stubs:\n{json.dumps(p_endpoints)}\n\nFunctions:\n{json.dumps(p_functions)}\n\nObjects:\n{json.dumps(p_objects)}\n\nCrash assessment:\n{json.dumps(p_assessment)}'
        },
    ]

    existing_names = set([s.name for s in harness.endpoints])
    original_stubs = {s.name: s for s in harness.endpoints}
    modified_stubs = {}

    for i in range(MAX_RETRIES):
        print(f'{Colors.GREEN}[+]{Colors.END} Patching fuzzer stubs (attempt {i+1})')
        response = client.responses.parse(
            model='gpt-5',
            input=ctx,
            reasoning={ "effort": "low" },
            text_format=PatcherResponse
        )
        ctx += response.output
        usage.record_model('patcher', 'gpt-5', UsageTokens.from_openai(response.usage))
        stubs = response.output_parsed.stubs

        # Validate the stubs in parallel
        results = parallel_run(threads, run_validate_stub, [(info, s, harness.objects) for s in stubs])
        failed_stubs = []

        for r in results:
            if isinstance(r.result, FunctionImpl):
                stub: FunctionImpl = r.result
                if stub.name in existing_names:
                    print(f'{Colors.GREEN}[+]{Colors.END} Modified stub for {Colors.YELLOW}{stub.name}{Colors.END}')
                    modified_stubs[stub.name] = stub
                    print_function_impl(stub)
                else:
                    print(f'{Colors.RED}[-]{Colors.END} Extraneous stub for {Colors.YELLOW}{stub.name}{Colors.END} (not in existing stubs)')
            else:
                print(f'{Colors.RED}[-]{Colors.END} Invalid stub for {Colors.YELLOW}{r.result.stub.name}{Colors.END}')
                failed_stubs.append(r.result)

        if len(failed_stubs) > 0:
            # Add to context
            ctx += [
                {
                    "role": "user",
                    "content": f'Several stubs failed to compile. Please regenerate only these ones to fix the following errors:\n{json.dumps([s.model_dump() for s in failed_stubs])}'
                },
            ]
            continue

        # We are done, break.
        break

    # Print the diffs for each modified stub
    for name, stub in modified_stubs.items():
        print(f'{Colors.GREEN}[+]{Colors.END} Modified stub for {Colors.YELLOW}{name}{Colors.END}')
        diff_lines = list(difflib.unified_diff(
            original_stubs[name].code.splitlines(),
            stub.code.splitlines(),
            lineterm='',
            n=100,
        ))
        diff_code = '\n'.join(diff_lines)
        pretty_print_code(diff_code, language="diff", title=f"Modified stub for {name}")

    orig = [original_stubs[name] for name in modified_stubs]
    return PatchInfo(original_stubs=orig, modified_stubs=modified_stubs.values(), usage=usage)


def run_patcher(info: ProjectInfo, harness: ProjectMetadata, assessment: CrashAssessment, threads: int) -> PatchInfo:
    # If we have too many endpoints, use a more restricted context for patching
    # Otherwise, we can use the full context
    print('[INFO] Using full context for patching')
    return run_patcher_full_context(info, harness, assessment, threads)


def main(args):
    info = ProjectInfo.model_validate_json(open(args.info).read())
    harness = ProjectMetadata.model_validate_json(open(args.harness).read())
    assessment = CrashAssessment.model_validate_json(open(args.assessment).read())
    patch_info = run_patcher(info, harness, assessment, args.threads)
    print(patch_info.usage.cost_by_task())

    with open(args.output, 'w') as f:
        f.write(patch_info.model_dump_json(indent=2))


def register(subparser: ArgumentParser):
    parser = subparser.add_parser('patcher')
    parser.add_argument('--info', type=str, help='Path to the project info file', default='info.json')
    parser.add_argument('--harness', type=str, required=True)
    parser.add_argument('--assessment', type=str, required=True)
    parser.add_argument('--output', type=str, required=True)
    parser.add_argument('--threads', type=int, default=1, help='Number of threads to use')
    parser.set_defaults(func=main)
