import json
from pydantic import BaseModel, Field
import os
from typing import List

from .data import FunctionInfo
from .util import get_model

from ..utils.colors import Colors
from ..data.metadata import UsageTracker, UsageTokens

from openai import OpenAI


RETRIES = 3

PROMPT_SCAN_FUNCTIONS_NAMES = open(os.path.join(os.path.dirname(__file__), 'prompts/scan_function_names.md')).read()
PROMPT_SCAN_FUNCTIONS = open(os.path.join(os.path.dirname(__file__), 'prompts/scan_functions.md')).read()


class FunctionsNamesResponse(BaseModel):
    functions: List[str] = Field(description="The list of function names")


class FunctionsResponse(BaseModel):
    functions: List[FunctionInfo]


def scan_function_names(code: str, usage: UsageTracker) -> List[str]:
    client = OpenAI()
    model = get_model()

    response = client.responses.parse(
        model=model,
        reasoning={ "effort": "low" },
        input=[
            {
                "role": "developer",
                "content": PROMPT_SCAN_FUNCTIONS_NAMES
            },
            {
                "role": "user",
                "content": code
            },
        ],
        text_format=FunctionsNamesResponse
    )

    usage.record_model('scan-function-names', model, UsageTokens.from_openai(response.usage))
    return response.output_parsed.functions


def scan_functions(code: str, names: List[str], usage: UsageTracker, retries: int = RETRIES) -> List[FunctionInfo]:
    out_functions = []
    remaining_names = [x for x in names]
    model = get_model()

    for i in range(retries):
        print(f'Scanning functions (step {i + 1} of {retries})...')
        client = OpenAI()
        response = client.responses.parse(
            model=model,
            reasoning={ "effort": "low" },
            input=[
                {
                    "role": "developer",
                    "content": PROMPT_SCAN_FUNCTIONS
                },
                {
                    "role": "user",
                    "content": f'Code:\n{code}'
                },
                {
                    "role": "user",
                    "content": f'Functions to scan:\n{list(remaining_names)}'
                },
            ],
            text_format=FunctionsResponse
        )
        usage.record_model('scan-functions', model, UsageTokens.from_openai(response.usage))
        functions = response.output_parsed.functions

        for f in functions:
            if f.name not in remaining_names:
                print(f'{Colors.RED}Function {Colors.YELLOW}{f.name}{Colors.END} not found in remaining names, skipping...')
                continue

            # Check if its invalid
            if f.error is not None:
                print(f'{Colors.RED}Function {Colors.YELLOW}{f.name}{Colors.END} is invalid: {f.error}')
                continue

            out_functions.append(f)
            remaining_names.remove(f.name)

        if len(remaining_names) == 0:
            break

    # Sort by name in original name order
    out_functions.sort(key=lambda x: names.index(x.name))
    return out_functions


def main(args):
    code = open(args.file).read()
    usage = UsageTracker()
    names = scan_function_names(code, usage)
    print(f'Found {len(names)} functions: {names}')

    functions = scan_functions(code, names, usage)
    print(usage.cost_by_task())
    print(f'Found {len(functions)} functions')

    functions = [x.model_dump() for x in functions]

    if args.output:
        with open(args.output, 'w') as f:
            f.write(json.dumps(functions, indent=2))
    else:
        print(json.dumps(functions, indent=2))


def register(subparsers):
    scan_parser = subparsers.add_parser('scan-functions')
    scan_parser.add_argument('-f', '--file', type=str, required=True)
    scan_parser.add_argument('-o', '--output', type=str, required=False)
    scan_parser.set_defaults(func=main)
