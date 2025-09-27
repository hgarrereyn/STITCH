from argparse import ArgumentParser
import os
import difflib
from typing import List, Dict
from pydantic import BaseModel, Field
from openai import OpenAI
from ..data.metadata import UsageTracker, UsageTokens
from .data import FunctionImpl, FunctionImplError
import json
from ..data.project_info import ProjectInfo
from .data import ProjectMetadata
from .distill_agent import CrashAssessment
from .pretty import print_functions, pretty_print_code, print_function_impl, pretty_print_error
from .validation import run_validate_stub
from .util import parallel_run
from ..utils.colors import Colors
import re


PROMPT_PATCHER_AGENT = open(os.path.join(os.path.dirname(__file__), 'prompts/patcher_agent.md')).read()

MAX_STEPS = 20

class PatchInfo(BaseModel):
    original_stubs: List[FunctionImpl]
    modified_stubs: List[FunctionImpl]
    usage: UsageTracker


def find_stubs(pattern: str, harness: ProjectMetadata) -> List[FunctionImpl]:
    matches = []
    for e in harness.endpoints:
        if re.search(pattern, e.name):
            matches.append(e)
        elif re.search(pattern, e.code):
            matches.append(e)
    return matches


def run_patcher_agent(info: ProjectInfo, harness: ProjectMetadata, assessment: CrashAssessment, threads: int) -> PatchInfo:
    usage = UsageTracker()
    client = OpenAI()

    valid_stub_names = set([e.name for e in harness.endpoints])
    endpoints_by_name = {e.name: e for e in harness.endpoints}

    p_objects = [o.model_dump() for o in harness.objects]
    p_functions = [f.model_dump() for f in harness.functions if f.name in assessment.relevant_functions]
    p_stubs = [e.model_dump() for e in harness.endpoints if e.name in assessment.relevant_functions]

    provided_stubs = set(assessment.relevant_functions)
    submitted_patches: Dict[str, FunctionImpl] = {}

    ctx = [
        {
            "role": "developer",
            "content": PROMPT_PATCHER_AGENT
        },
        {
            "role": "user",
            "content": f'Objects: {json.dumps(p_objects)}\nRelevant functions: {json.dumps(p_functions)}\nRelevant stubs: {json.dumps(p_stubs)}\nCrash assessment: \n```\n{assessment.model_dump_json(indent=2)}\n```'
        }
    ]

    tools = [
        {
            "type": "function",
            "name": "find_stubs",
            "description": "Search the codebase for fuzzer stubs matching the pattern",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": { "type": "string", "description": "The pattern to search for (re.search syntax)" }
                },
                "required": ["pattern"]
            }
        },
        {
            "type": "function",
            "name": "patch_stubs",
            "description": "Submit a new list of patches, any compilation errors will be returned back",
            "parameters": {
                "type": "object",
                "properties": {
                    "stubs": {
                        "type": "array",
                        "description": "The new list of patches",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": { "type": "string", "description": "The name of the stub" },
                                "inputs": {
                                    "type": "array",
                                    "description": "The inputs of the stub",
                                    "items": { 
                                        "type": "object",
                                        "properties": {
                                            "name": { "type": "string", "description": "The name of the input" },
                                            "type": { "type": "string", "description": "The type of the input" }
                                        },
                                        "required": ["name", "type"]
                                    }
                                },
                                "outputs": {
                                    "type": "array",
                                    "description": "The outputs of the stub",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "name": { "type": "string", "description": "The name of the output" },
                                            "type": { "type": "string", "description": "The type of the output" }
                                        },
                                        "required": ["name", "type"]
                                    },
                                },
                                "code": { "type": "string", "description": "The code of the stub" }
                            },
                            "required": ["name", "inputs", "outputs", "code"]
                        },
                    }
                },
                "required": ["stubs"]
            }
        },
        {
            "type": "function",
            "name": "crash_pattern",
            "description": "Note down the general crash pattern observed for future reference",
            "parameters": {
                "type": "object",
                "properties": { "theme": { "type": "string", "description": "The general crash pattern observed" } },
                "required": ["theme"]
            }
        }
    ]

    for i in range(MAX_STEPS):
        print(f'{Colors.GREEN}[+]{Colors.END} Patching fuzzer stubs (step {i+1} of {MAX_STEPS})')
        response = client.responses.parse(
            model='gpt-5',
            input=ctx,
            reasoning={
                "effort": "medium",
                "summary": "auto"
            },
            tools=tools
        )
        ctx += response.output
        usage.record_model('patcher', 'gpt-5', UsageTokens.from_openai(response.usage))

        did_tool_call = False
        for message in response.output:
            match message.type:
                case 'reasoning':
                    if message.summary is not None and len(message.summary) > 0:
                        print(f'{Colors.PURPLE}[*]{Colors.END} Reasoning: {message.summary[0].text}')
                case 'function_call':
                    did_tool_call = True
                    match message.name:
                        case 'find_stubs':
                            args = json.loads(message.arguments)

                            err = False
                            stubs = []
                            try:
                                stubs = find_stubs(args['pattern'], harness)
                            except Exception as e:
                                print(f'{Colors.RED}[-]{Colors.END} Error searching for stubs: {e}')
                                stubs = []
                                err = True

                                ctx.append({
                                    "type": "function_call_output",
                                    "call_id": message.call_id,
                                    "output": json.dumps({
                                        "error": str(e)
                                    })
                                })

                            if not err:
                                print(f'{Colors.GREEN}[+]{Colors.END} Searching for stubs: "{args["pattern"]}" -> Found {len(stubs)} stubs')

                                already_seen = [x.name for x in stubs if x.name in provided_stubs]
                                new_stubs = [x.model_dump() for x in stubs if x.name not in already_seen]

                                ctx.append({
                                    "type": "function_call_output",
                                    "call_id": message.call_id,
                                    "output": json.dumps({
                                        "seen_matches": already_seen,
                                        "new_matches": new_stubs
                                    })
                                })
                        case 'patch_stubs':
                            args = json.loads(message.arguments)
                            stubs = args['stubs']

                            print(f'{Colors.GREEN}[+]{Colors.END} Patching stubs: {len(stubs)} stubs')

                            valid_stubs = []
                            failed_stubs = []

                            impls = []
                            for stub in stubs:
                                try:
                                    fimpl = FunctionImpl.model_validate(stub)
                                except Exception as e:
                                    print(f'{Colors.RED}[-]{Colors.END} Invalid stub: {e}')
                                    failed_stubs.append({
                                        "name": stub['name'],
                                        "error": 'Invalid stub syntax'
                                    })
                                    continue

                                if fimpl.name not in valid_stub_names:
                                    failed_stubs.append({
                                        "name": fimpl.name,
                                        "error": 'Stub name not found'
                                    })
                                    continue

                                impls.append(fimpl)
                            
                            # Validate the stubs in parallel
                            results = parallel_run(threads, run_validate_stub, [(info, s, harness.objects) for s in impls])
                            
                            for r in results:
                                if isinstance(r.result, FunctionImpl):
                                    valid_stubs.append(r.result.model_dump())

                                    stub: FunctionImpl = r.result

                                    # Print the diffs for each modified stub
                                    print(f'{Colors.GREEN}[+]{Colors.END} Modified stub for {Colors.YELLOW}{stub.name}{Colors.END}')
                                    diff_lines = list(difflib.unified_diff(
                                        endpoints_by_name[stub.name].code.splitlines(),
                                        stub.code.splitlines(),
                                        lineterm='',
                                        n=100,
                                    ))
                                    diff_code = '\n'.join(diff_lines)
                                    pretty_print_code(diff_code, language="diff", title=f"Modified stub for {stub.name}")
                                else:
                                    error: FunctionImplError = r.result
                                    print(f'{Colors.RED}[-]{Colors.END} Failed to validate stub: {error.stub.name}')
                                    failed_stubs.append(r.result.model_dump())
                                    
                            submitted_patches.update({s['name']: FunctionImpl.model_validate(s) for s in valid_stubs})

                            ctx.append({
                                "type": "function_call_output",
                                "call_id": message.call_id,
                                "output": json.dumps({
                                    "valid_patches": valid_stubs,
                                    "failed_patches": failed_stubs
                                })
                            })
                        case 'crash_pattern':
                            args = json.loads(message.arguments)
                            print(f'{Colors.GREEN}[+]{Colors.END} Crash pattern: {args["theme"]}')
                            ctx.append({
                                "type": "function_call_output",
                                "call_id": message.call_id,
                                "output": 'ack'
                            })
                        case _:
                            print(f'{Colors.RED}[-]{Colors.END} Unknown tool call: {message.name}')

        if not did_tool_call:
            print(f'{Colors.RED}[-]{Colors.END} No tool call made, exiting')
            break

    print(f'{Colors.GREEN}[+]{Colors.END} Patched fuzzer stubs: {len(submitted_patches)}')

    original_stubs = [endpoints_by_name[e] for e in submitted_patches.keys()]
    modified_stubs = [submitted_patches[e] for e in submitted_patches.keys()]

    return PatchInfo(original_stubs=original_stubs, modified_stubs=modified_stubs, usage=usage)


def main(args):
    info = ProjectInfo.model_validate_json(open(args.info).read())
    harness = ProjectMetadata.model_validate_json(open(args.harness).read())
    assessment = CrashAssessment.model_validate_json(open(args.assessment).read())
    patch_info = run_patcher_agent(info, harness, assessment, args.threads)
    print(patch_info.usage.cost_by_task())

    with open(args.output, 'w') as f:
        f.write(patch_info.model_dump_json(indent=2))


def register(subparser: ArgumentParser):
    parser = subparser.add_parser('patcher-agent')
    parser.add_argument('--info', type=str, help='Path to the project info file', default='info.json')
    parser.add_argument('--harness', type=str, required=True)
    parser.add_argument('--assessment', type=str, required=True)
    parser.add_argument('--output', type=str, required=True)
    parser.add_argument('--threads', type=int, default=1, help='Number of threads to use')
    parser.set_defaults(func=main)
