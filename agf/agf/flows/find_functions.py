from typing import List

# from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from agf.data.project_info import ProjectInfo
from agf.data.metadata import FunctionInfo, SchemaMetadata, ObjectInfo
from agf.utils.colors import Colors
from agf.utils.general import split_into_chunks
from agf.build.validator import validate_function


PROMPT_SCAN_CHUNK = """
You are an expert C/C++ developer.

Examine the following chunk of C/C++ code and find all public functions defined in the code.

For C++ code, include the namespace and/or class name if relevant.
E.g. `my_namespace::my_class::my_function`

For C code, just include the function name.

Return a list of functions with the following fields:
- `name`: the name of the function
- `signature`: the C/C++ signature of the function including the return type and argument names
- `description`: a short description of the function
- `usage_requirements`: a list of requirements for using the function, human readable
- `preconditions`: a list of Precondition(...) required to execute this function
- `effects`: a list of Effect(...) that describe the changes made to attributes on objects
- `imposes`: a list of Impose(...) that describe long-lasting constraints on the state of the system

For usage requirements, include human-readable requirements on:
- Requirements on when the function can be called (i.e. ordering constraints)
- Requirements on the argument ranges or values
- Requirements on the state input objects can be in (i.e. initialized, prepared, etc...)
- Requirements on what the caller must do after the function call (i.e. cleanup in a certain way)
- Any other requirements or restrictions on the function

Then given, these requirements, try to encode each of them (if possible) into one of the following concepts:
You must follow the following format, replacing `a` and `b` with argument names, and `x` with a ghost attribute name.
You must only use ghost attributes that are defined in the list of objects below.

- preconditions:
    - `Precondition(Eq(a.x, v))`: the function must be called when a.x is equal to v
    - `Precondition(Initializer)`: the function must be called first thing in the program
    - `Precondition(Finalizer)`: the function must be called last thing in the program
- effects:
    - `Effect(Set(a.x, v))`: after the function is called, a.x is equal to v
    - `Effect(Inc(a.x))`: after the function is called, a.x is incremented by 1
    - `Effect(Dec(a.x))`: after the function is called, a.x is decremented by 1
- imposes:
    - `Impose(Outlives(a, b))`: the function imposes the constraint that a outlives b (i.e. a should not be deleted before b)

In these examples, `a` and `b` are the names of arguments to the function.


Notes:
- Include each function only once, do not include multiple variations of the same function.
- Include only the function name, not the function signature.

[CODE]
{code}
[/CODE]

[OBJECTS]
{objects}
[/OBJECTS]
"""


class FunctionsResponse(BaseModel):
    functions: List[FunctionInfo]


def _scan_chunk(info: ProjectInfo, model: str, chunk: str, objects: List[ObjectInfo]) -> List[FunctionInfo]:
    llm = ChatOpenAI(model=model).with_structured_output(FunctionsResponse, strict=True)
    res: FunctionsResponse = llm.invoke(PROMPT_SCAN_CHUNK.format(code=chunk, objects=objects))
    return res.functions


def _validate_function(info: ProjectInfo, function: FunctionInfo) -> bool:
    """Ensure we can instantiate this object in code."""
    return validate_function(function.name, info) is None


def find_functions(info: ProjectInfo, model: str, headers: List[str], objects: List[ObjectInfo]) -> List[FunctionInfo]:
    def log(msg: str):
        print(f'[{Colors.BOLD}{Colors.GREEN}FIND{Colors.END}] {msg}')

    log('Splitting into chunks...')
    chunks = split_into_chunks(headers, 5000)
    log(f'Split into {len(chunks)} chunks')

    log('Scanning chunks...')
    functions = []
    for chunk in chunks:
        functions.extend(_scan_chunk(info, model, chunk, objects))

    log(f'Found {len(functions)} functions')

    # Merge functions with the same signature
    merged = []
    seen_sigs = set()
    for func in functions:
        if func.signature not in seen_sigs:
            merged.append(func)
            seen_sigs.add(func.signature)

    log(f'Merged {len(functions)} functions into {len(merged)} unique functions')
    functions = merged

    log('Validating functions...')
    valid_functions = []
    for func in functions:
        if _validate_function(info, func):
            valid_functions.append(func)
            log(f'Check {func.name}: {Colors.GREEN}VALID{Colors.END}')
        else:
            log(f'Check {func.name}: {Colors.RED}INVALID{Colors.END}')

    log(f'Found {len(valid_functions)} valid functions')
    return valid_functions


def main(args):
    schema = SchemaMetadata.model_validate_json(open(args.objects).read())
    info = ProjectInfo.model_validate_json(open(args.info).read())
    functions = find_functions(info, args.model, args.headers, schema.objects)

    # Write to file if provided
    meta = SchemaMetadata(functions=functions)
    if args.output:
        with open(args.output, 'w') as f:
            f.write(meta.model_dump_json(indent=2))
    else:
        print(meta.model_dump_json(indent=2))


def register(subparsers):
    parser = subparsers.add_parser('find-functions', help='Find functions in the codebase')
    parser.add_argument('--objects', type=str, default=None, help='Path to the objects file')
    parser.add_argument('--info', type=str, default='info.json', help='Path to the project info file')
    parser.add_argument('--headers', type=str, nargs='+', required=True, help='List of header files to scan')
    parser.add_argument('--model', type=str, default='gpt-4.1-nano', help='Model to use for scanning')
    parser.add_argument('--output', type=str, default=None, help='Path to the output file')
    parser.set_defaults(func=main)
