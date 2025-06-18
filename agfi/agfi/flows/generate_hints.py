
from typing import List
from enum import Enum

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from ..data.project_info import ProjectInfo
from ..data.metadata import FunctionStubTemplate, SchemaMetadata, FunctionStubMapping, FunctionStub, FunctionArgumentUsage, ObjectStub, FunctionStubHints
from ..utils.colors import Colors
from ..utils.general import split_into_chunks
from ..build.validator import validate_function, format_and_check

BATCH_SIZE = 40


PROMPT_TEMPLATE = """
You are an expert C/C++ fuzzer engineer.

You will be given a list of objects and functions sourced from a C/C++ codebase.
Each function has an associated `stub` -- a short code snippet that fuzzes the function.
Your job is to generate a list of `hints` for each function stub that uses the `FUZZ_PARAM_STR` or `FUZZ_PARAM_FILE` macros.

Generate hints for **every** function using the macros, do not skip any functions or exit early.

Follow these guidelines:

[GUIDELINES]
In order to fuzz effectively, we want to initialize buffers and files with interesting data that is likely to exercise functionality in the codebase.

A `hint` is a string that will be used to initialize a buffer or file.
Each `hint` should be a minimal example that is likely to exercise functionality or edge-cases in the function of interest.
Cumulatively, hints for a function should cover a wide range of diverse behaviors.

Instructions:
1. Identify all the functions that use the `FUZZ_PARAM_STR` or `FUZZ_PARAM_FILE` macros.
2. For each of these functions, look at the `stub` to understand how the buffer or file is used (there may be multiple buffers or files).
3. Try to understand what "valid" or "interesting" data would look like for each buffer or file. (i.e. are these string commands? SQL? JSON? ASCII art? etc...)
4. Generate a diverse list of at least 10 `hints` for each function stub that are likely to exercise the function in different ways.
5. If the function stub does not use the `FUZZ_PARAM_STR` or `FUZZ_PARAM_FILE` macros, return an empty list.
[/GUIDELINES]

[HINT_ENCODING]
You can encode hints as either a raw JSON string (for printable strings) or a hex string (for non-printable buffers).

For example, a printable string hint might look like:
```
{{
    "type": "raw",
    "hint": "Hello, world!"
}}
```

A non-printable buffer hint might look like (encoding the buffer `\x00\x00123\x00\x00`):
```
{{
    "type": "hex",
    "hint": "00003132330000"
}}
```
[/HINT_ENCODING]

[OBJECTS]
{objects}
[/OBJECTS]

[FUNCTIONS]
{functions}
[/FUNCTIONS]
"""


class FunctionInfo(BaseModel):
    name: str
    signature: str
    description: str
    requirements: List[str]
    stub: str


class FunctionHintType(str, Enum):
    RAW = "raw"
    HEX = "hex"

class FunctionHint(BaseModel):
    type: FunctionHintType
    hint: str = Field(description="Hint buffer. If type is RAW, this is a string. If type is HEX, this is a hex string.")

    @property
    def hint_bytes(self) -> bytes:
        if self.type == FunctionHintType.RAW:
            return self.hint.encode('utf-8')
        elif self.type == FunctionHintType.HEX:
            return bytes.fromhex(self.hint)
        else:
            raise ValueError(f"Invalid hint type: {self.type}")

class FunctionHints(BaseModel):
    name: str
    hints: List[FunctionHint]

    def to_stub_hints(self) -> FunctionStubHints:
        hints = []
        for hint in self.hints:
            try:
                hints.append(hint.hint_bytes.hex())
            except Exception as e:
                print(f'{Colors.RED}Error converting hint to hex: {Colors.YELLOW}{hint.hint}{Colors.END}')
                continue

        return FunctionStubHints(
            name=self.name,
            hints=hints
        )

class HintsResponse(BaseModel):
    functions: List[FunctionHints]


def _create_function_info(func: FunctionStub, template: FunctionStubTemplate) -> FunctionInfo:
    info = FunctionInfo(
        name=func.name,
        signature=func.signature,
        description=func.description,
        requirements=func.requirements,
        stub=template.stub
    )
    return info


def _gen_hints(info: ProjectInfo, meta: SchemaMetadata, model: str) -> List[FunctionStubHints]:
    def log(msg: str):
        print(f'[{Colors.GREEN}HINT{Colors.END}] {msg}')
    
    remaining_functions = {x.name: x for x in meta.functions}
    all_hints = []

    funcs = {x.name: x for x in meta.functions}
    templates = {x.name: x for x in meta.template_functions}

    infos = []

    for name in funcs:
        if name not in templates:
            log(f'{Colors.RED}Function {Colors.YELLOW}{name}{Colors.END} not found in templates, skipping...')
            continue

        infos.append(_create_function_info(funcs[name], templates[name]))

    log(f'Generating {Colors.YELLOW}{len(infos)}{Colors.END} hints...')

    for idx in range(0, len(infos), BATCH_SIZE):
        batch = infos[idx:idx+BATCH_SIZE]
        log(f'Generating batch {idx // BATCH_SIZE + 1} of {((len(infos) - 1) // BATCH_SIZE) + 1}...')

        llm = ChatOpenAI(model=model).with_structured_output(HintsResponse, strict=True)
        res: HintsResponse = llm.invoke(PROMPT_TEMPLATE.format(objects=meta.objects, functions=batch))

        for hints in res.functions:
            if hints.name not in remaining_functions:
                log(f'{Colors.RED}Function {Colors.YELLOW}{hints.name}{Colors.END} not found in metadata, skipping...')
                continue

            hints = hints.to_stub_hints()

            print(f'[{Colors.YELLOW}{hints.name}{Colors.END}]')
            for hint in hints.hints:
                print(f' - {bytes.fromhex(hint)}')
            
            all_hints.append(hints)
            remaining_functions.pop(hints.name)

    log(f'{Colors.GREEN}Generated {Colors.YELLOW}{len(all_hints)}{Colors.END} hints...')
    log(f'{Colors.RED}Failed to generate {Colors.YELLOW}{len(remaining_functions)}{Colors.END} hints...')
    return all_hints


def main(args):
    info = ProjectInfo.model_validate_json(open(args.info).read())
    meta = SchemaMetadata.model_validate_json(open(args.meta).read())

    hints = _gen_hints(info, meta, args.model)
    print(hints)

    meta.hints_functions = hints
    if args.output:
        with open(args.output, 'w') as f:
            f.write(meta.model_dump_json(indent=2))
    else:
        print(meta.model_dump_json(indent=2))


def register(subparsers):
    parser = subparsers.add_parser('gen-hints', help='Generate hints for the codebase')
    parser.add_argument('--info', type=str, default='info.json', help='Path to the project info file')
    parser.add_argument('--meta', type=str, required=True, help='Path to the schema metadata file')
    parser.add_argument('--model', type=str, default='gpt-4.1-mini', help='Model to use for scanning')
    parser.add_argument('--output', type=str, default=None, help='Path to the output file')
    parser.set_defaults(func=main)
