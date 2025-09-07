from pydantic import BaseModel
from typing import List, Tuple

from ..data.project_info import ProjectInfo
from ..data.metadata import UsageTracker, UsageMetrics
from ..build.validator import validate_function
from .data import FunctionEntry, FunctionEntryBatch, FunctionStub

from langchain_openai import ChatOpenAI
from langchain_core.callbacks import get_usage_metadata_callback


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

In the description, be specific about the function's purpose, how it is expected to be used, and any other relevant information.

In the requirements, list individual requirements for the user to follow about using the function, such as:
- when it can be called (i.e. ordering constraints)
- what the caller must do after the function call (i.e. cleanup in a certain way)
- restrictions on argument ranges or values provided by the caller (i.e. input "a" must be a positive integer)
- any other requirements or restrictions on the function

In the requirements, do not include information about what the function will do, only what the caller must do.
[/GUIDELINES]

[FUNCTIONS]
{functions}
[/FUNCTIONS]

[CODE]
{code}
[/CODE]
"""

PROMPT_SCAN_BATCH = """
You are an expert C/C++ developer.

Examine the following C/C++ header file and generate a thorough description of each of the requested functions.
Follow the guidelines below.
Process every function, do not skip any functions, do not stop early.

[GUIDELINES]
For each function, return the following fields:
- `name`: the name of the function (verbatim from the request)
- `signature`: the C/C++ signature of the function including the return type and argument names
- `description`: a brief description of the function, describing what it does, where it is expected to be used, similar functions, and any other relevant information
- `requirements`: a list of human-readable requirements for the function (see REQUIREMENTS below)
[/GUIDELINES]

[REQUIREMENTS]
From the information provided in the header file, and any information you can infer from the code, related functions, or patterns, generate a detailed list of requirements:

For example, all of the following:
- Restrictions on when the function can be called (i.e. before all other functions, after all other functions, etc...)
- Restrictions on how many times the function can be called (i.e. once, at most once, etc...)
- Restrictions on the state of the input objects (i.e. initialized, prepared, etc...) or whether they must be specific subtypes of a given type
- Restrictions on argument ranges or values provided by the caller (i.e. input "a" must be a positive integer)
- Restrictions on what the caller must do after the function call (i.e. cleanup in a certain way)
- Restrictions or information on how to detect errors or failures (i.e. return values, exceptions, error parameters, etc...)
- Any other requirements or restrictions on the function

The requirements should be as detailed as possible, and will be used to generate a test case for the function, so be as specific as possible.

The requirements should be in the form of a list of strings, each describing a requirement.
[/REQUIREMENTS]

[FUNCTIONS]
{functions}
[/FUNCTIONS]

[CODE]
{code}
[/CODE]
"""


class FirstScanResponse(BaseModel):
    functions: List[str]


class ScanBatchResponse(BaseModel):
    functions: List[FunctionStub]


def scan_functions(model: str, name: str, code: str, usage: UsageTracker) -> List[FunctionEntry]:
    llm = ChatOpenAI(model=model).with_structured_output(FirstScanResponse, strict=True)
    with get_usage_metadata_callback() as cb:
        res: FirstScanResponse = llm.invoke(PROMPT_FIRST_SCAN.format(code=code))
        usage.record('find-functions (first-scan)', UsageMetrics.from_usage(cb.usage_metadata))
    return [FunctionEntry(header=name, name=f) for f in res.functions]


def run_validate_function(info: ProjectInfo, entry: FunctionEntry) -> List[FunctionEntry]:
    if validate_function(entry.name, info) is None:
        return [entry]
    return []


def scan_function_info_batch(model: str, batch: FunctionEntryBatch, usage: UsageTracker) -> List[FunctionStub]:
    llm = ChatOpenAI(model=model).with_structured_output(ScanBatchResponse, strict=True)
    with get_usage_metadata_callback() as cb:
        res: ScanBatchResponse = llm.invoke(PROMPT_SCAN_BATCH.format(code=batch.header_code, functions=[x.name for x in batch.entries]))
        usage.record('find-functions (scan-batch)', UsageMetrics.from_usage(cb.usage_metadata))
    return res.functions
