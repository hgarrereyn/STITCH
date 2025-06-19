from typing import List, Tuple
import concurrent.futures

from langchain_core.callbacks import get_usage_metadata_callback
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from ..data.project_info import ProjectInfo
from ..data.metadata import FunctionStub, SchemaMetadata, UsageMetrics, UsageTracker
from ..utils.colors import Colors
from ..utils.general import merge_headers
from ..build.validator import validate_function



PROMPT_FIRST_SCAN = """
You are an expert C/C++ developer.

Examine the following chunk of C/C++ code and find all public functions defined in the code.
Follow the guidelines below.
Find every function, do not skip any functions, do not stop early.

[GUIDELINES]
For every public API function, return the function name, fully qualified:
- For C++ code, include the namespace and/or class name if relevant (e.g. `my_namespace::my_class::my_function`).
- For C code, just include the function name (e.g. `my_function`).

Return a complete list of function names.
[/GUIDELINES]

[CODE]
{code}
[/CODE]
"""


PROMPT_SCAN_CHUNK = """
You are an expert C/C++ developer.

Examine the following chunk of C/C++ code and generate a thorough description of each of the requested functions.
Follow the guidelines below.
Process every function, do not skip any functions, do not stop early.

[GUIDELINES]
For each function, return the following fields:
- `name`: the name of the function (verbatim from the request)
- `signature`: the C/C++ signature of the function including the return type and argument names
- `description`: a detailed description of the function
- `requirements`: human-readable requirements for the function (e.g. restrictions on when it can be called, argument ranges or values, etc.)

In the description, make sure to address the following points:
- Requirements on when the function can be called (i.e. ordering constraints)
- Requirements on the state input objects can be in (i.e. initialized, prepared, etc...)
- Requirements on what the caller must do after the function call (i.e. cleanup in a certain way)
- Any other requirements or restrictions on the function
[/GUIDELINES]

[FUNCTIONS]
{functions}
[/FUNCTIONS]

[CODE]
{code}
[/CODE]
"""


class FirstScanResponse(BaseModel):
    functions: List[str]


class FunctionsResponse(BaseModel):
    functions: List[FunctionStub]


def _first_scan(info: ProjectInfo, model: str, chunk: str, usage: UsageTracker) -> List[str]:
    llm = ChatOpenAI(model=model).with_structured_output(FirstScanResponse, strict=True)
    with get_usage_metadata_callback() as cb:
        res: FirstScanResponse = llm.invoke(PROMPT_FIRST_SCAN.format(code=chunk))
        usage.record('find-functions (first-scan)', UsageMetrics.from_usage(cb.usage_metadata))
    return res.functions


def _scan_functions(info: ProjectInfo, model: str, chunk: str, functions: List[str]) -> Tuple[List[FunctionStub], UsageMetrics]:
    llm = ChatOpenAI(model=model).with_structured_output(FunctionsResponse, strict=True)
    with get_usage_metadata_callback() as cb:
        res: FunctionsResponse = llm.invoke(PROMPT_SCAN_CHUNK.format(code=chunk, functions=functions))
        usage = UsageMetrics.from_usage(cb.usage_metadata)
    return res.functions, usage


def _validate_function(info: ProjectInfo, function: FunctionStub) -> bool:
    """Ensure we can instantiate this object in code."""
    return validate_function(function.name, info) is None


def find_functions(info: ProjectInfo, model: str, headers: List[str], threads: int, usage: UsageTracker) -> List[FunctionStub]:
    def log(msg: str):
        print(f'[{Colors.BOLD}{Colors.GREEN}FIND{Colors.END}] {msg}')

    function_names = set()

    for header in headers:
        print(f'[{Colors.BOLD}{Colors.GREEN}FIND{Colors.END}] Processing {header}')
        with open(header, 'r') as f:
            code = f.read()
        function_names.update(_first_scan(info, model, code, usage))

    function_names = list(function_names)
    log(f'Found {len(function_names)} functions')

    log('Merging headers...')
    merged_headers = merge_headers(headers)
    log(f'Merged {len(headers)} headers into {len(merged_headers)} lines')

    batch_size = 40
    batches = []
    for idx in range(0, len(function_names), batch_size):
        batch = function_names[idx:idx+batch_size]
        batches.append((idx, batch))

    functions = []

    def process_batch(args):
        idx, batch = args
        log(f'Scanning batch {idx // batch_size + 1} of {(len(function_names) // batch_size) + 1}...')
        result = _scan_functions(info, model, merged_headers, batch)
        return (idx, result)

    with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as executor:
        future_to_idx = {executor.submit(process_batch, args): args[0] for args in batches}
        results = []
        for future in concurrent.futures.as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                batch_idx, (batch_functions, batch_usage) = future.result()
                functions.extend(batch_functions)
                usage.record('find-functions', batch_usage)
                log(f'Found {len(functions)} functions')
            except Exception as exc:
                log(f'Batch {idx // batch_size + 1} generated an exception: {exc}')

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
    info = ProjectInfo.model_validate_json(open(args.info).read())
    functions = find_functions(info, args.model, args.headers)

    # Write to file if provided
    meta = SchemaMetadata(functions=functions)
    if args.output:
        with open(args.output, 'w') as f:
            f.write(meta.model_dump_json(indent=2))
    else:
        print(meta.model_dump_json(indent=2))


def register(subparsers):
    parser = subparsers.add_parser('find-functions', help='Find functions in the codebase')
    parser.add_argument('--info', type=str, default='info.json', help='Path to the project info file')
    parser.add_argument('--headers', type=str, nargs='+', required=True, help='List of header files to scan')
    parser.add_argument('--model', type=str, default='gpt-4.1-mini', help='Model to use for scanning')
    parser.add_argument('--output', type=str, default=None, help='Path to the output file')
    parser.set_defaults(func=main)
