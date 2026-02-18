from __future__ import annotations

import json
from pydantic import BaseModel, Field
import os
from typing import List, Optional, TYPE_CHECKING

from .data import FunctionInfo
from .util import call_llm, get_model

from ..data.metadata import UsageTracker

if TYPE_CHECKING:
    from .ui import TokenProgress


RETRIES = 3

PROMPT_SCAN_FUNCTIONS_NAMES = open(os.path.join(os.path.dirname(__file__), 'prompts/scan_function_names.md')).read()
PROMPT_SCAN_FUNCTIONS = open(os.path.join(os.path.dirname(__file__), 'prompts/scan_functions.md')).read()


class FunctionsNamesResponse(BaseModel):
    functions: List[str] = Field(description="The list of function names")


class FunctionsResponse(BaseModel):
    functions: List[FunctionInfo]


def scan_function_names(code: str, usage: UsageTracker, token_progress: Optional[TokenProgress] = None) -> List[str]:
    parsed: FunctionsNamesResponse = call_llm(
        input=[
            {"role": "developer", "content": PROMPT_SCAN_FUNCTIONS_NAMES},
            {"role": "user", "content": code},
        ],
        text_format=FunctionsNamesResponse,
        usage=usage,
        task='scan-function-names',
        reasoning={"effort": "low"},
        token_progress=token_progress,
    )
    return parsed.functions


def scan_functions(code: str, names: List[str], usage: UsageTracker, retries: int = RETRIES, quiet: bool = False, token_progress: Optional[TokenProgress] = None) -> List[FunctionInfo]:
    from . import ui

    out_functions = []
    remaining_names = [x for x in names]
    model = get_model()

    for i in range(retries):
        if not quiet:
            ui.info(f"Scanning functions (pass {i + 1} of {retries})")

        parsed: FunctionsResponse = call_llm(
            model=model,
            input=[
                {"role": "developer", "content": PROMPT_SCAN_FUNCTIONS},
                {"role": "user", "content": f'Code:\n{code}'},
                {"role": "user", "content": f'Functions to scan:\n{list(remaining_names)}'},
            ],
            text_format=FunctionsResponse,
            usage=usage,
            task='scan-functions',
            reasoning={"effort": "low"},
            token_progress=token_progress,
        )
        functions = parsed.functions

        for f in functions:
            if f.name not in remaining_names:
                continue

            if f.error is not None:
                if not quiet:
                    ui.warning(f"Function [yellow]{f.name}[/yellow] invalid: {f.error}")
                continue

            out_functions.append(f)
            remaining_names.remove(f.name)

        if len(remaining_names) == 0:
            break

    out_functions.sort(key=lambda x: names.index(x.name))
    return out_functions


def main(args):
    from . import ui

    code = open(args.file).read()
    usage = UsageTracker()
    names = scan_function_names(code, usage)
    ui.log('SCAN', f"Found [cyan]{len(names)}[/cyan] function names")

    functions = scan_functions(code, names, usage)
    print(usage.cost_by_task())
    ui.log('SCAN', f"Found [cyan]{len(functions)}[/cyan] functions")

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
