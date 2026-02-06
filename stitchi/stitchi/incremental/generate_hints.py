from typing import List
from enum import Enum

from langchain_core.callbacks import get_usage_metadata_callback
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from ..utils.colors import Colors
from ..data.metadata import UsageTracker, UsageMetrics
from ..data.project_info import ProjectInfo  # kept for potential future use
from .data import ObjectStub, FunctionImpl, FunctionHints as EndpointHints

# The number of endpoints processed by the LLM in a single request
BATCH_SIZE = 40

PROMPT_TEMPLATE = """
You are an expert C/C++ fuzzer engineer.

You will be given a list of objects and endpoint templates sourced from a C/C++ codebase.
Each endpoint has an associated `template` -- a C/C++ code snippet that fuzzes the underlying API function.
Your job is to generate a list of `hints` for each endpoint template that uses the `FUZZ_PARAM_STR` or `FUZZ_PARAM_FILE` macros.

Generate hints for **every** endpoint using the macros -- do not skip any endpoints or exit early.

Follow these guidelines:
[GUIDELINES]
In order to fuzz effectively, we want to initialize buffers and files with interesting data that is likely to exercise functionality in the codebase.

A `hint` is a string that will be used to initialize a buffer or file.
Each `hint` should be a minimal example that is likely to exercise functionality or edge-cases in the function of interest.
Cumulatively, hints for an endpoint should cover a wide range of diverse behaviours.
Instructions:
1. Identify all the templates that use the `FUZZ_PARAM_STR` or `FUZZ_PARAM_FILE` macros, and note their sizes.
    - hints for `FUZZ_PARAM_STR` should be used to initialize random strings.
    - hints for `FUZZ_PARAM_FILE` should be used to initialize random file contents (the filepath itself is managed by the fuzzer).
2. For each of these templates, look at the template to understand how the buffer or file is used (there may be multiple buffers or files).
3. Think about what "valid" or "interesting" data looks like for each buffer or file (e.g. string commands, SQL, JSON, ASCII art, etc.).
4. Generate a diverse list of at least 15 `hints` for each template that are likely to exercise the function in different ways. Some of these hints can be long (e.g. for interesting file data).
5. If the template does not use the `FUZZ_PARAM_STR` or `FUZZ_PARAM_FILE` macros, return an empty list.
[/GUIDELINES]

[HINT_ENCODING]
Encode hints as one of two formats:
 • RAW -- for printable strings (JSON string)
 • HEX -- for non-printable buffers (hex string)

Example RAW hint:
```
{{
    "type": "raw",
    "hint": "Hello, world!"
}}
```

Example HEX hint for the buffer `\x00\x00123\x00\x00`:
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

[ENDPOINTS]
{endpoints}
[/ENDPOINTS]
"""


class EndpointInfo(BaseModel):
    """Minimal information about an endpoint required to generate hints."""
    name: str
    template: str


class FunctionHintType(str, Enum):
    RAW = "raw"
    HEX = "hex"


class FunctionHint(BaseModel):
    type: FunctionHintType
    hint: str = Field(description="Hint buffer. If type is RAW, this is a printable string. If type is HEX, this is a hex string.")

    @property
    def hint_bytes(self) -> bytes:
        if self.type == FunctionHintType.RAW:
            return self.hint.encode("utf-8")
        elif self.type == FunctionHintType.HEX:
            return bytes.fromhex(self.hint)
        else:
            raise ValueError(f"Invalid hint type: {self.type}")


class FunctionHints(BaseModel):
    name: str
    hints: List[FunctionHint]


class HintsResponse(BaseModel):
    functions: List[FunctionHints]


def _log(msg: str):
    print(f"[{Colors.GREEN}HINT{Colors.END}] {msg}")


def run_generate_hints_batch(model: str, endpoints: List[FunctionImpl], objects: List[ObjectStub], usage: UsageTracker) -> List[EndpointHints]:
    """Generate buffer/file hints for a batch of endpoints.

    Args:
        model: The LLM model name.
        endpoints: A batch of validated `FunctionImpl` objects.
        objects: The list of recognised `ObjectStub`s.
        usage: A shared UsageTracker instance to record token usage.

    Returns:
        A list of `EndpointHints` entries with hex-encoded hints.
    """

    if not endpoints:
        return []

    _log(f"Generating hints for batch of {len(endpoints)} endpoints…")

    # Prepare the minimal endpoint data for the prompt
    infos = [EndpointInfo(name=e.name, template=e.code) for e in endpoints]

    llm = ChatOpenAI(model=model).with_structured_output(HintsResponse, strict=True)
    with get_usage_metadata_callback() as cb:
        res: HintsResponse = llm.invoke(
            PROMPT_TEMPLATE.format(
                objects=[o.model_dump_json() for o in objects],
                endpoints=[info.model_dump_json() for info in infos],
            )
        )
        # Record token usage per-task so the caller can aggregate later
        usage.record("gen-endpoint-hints", UsageMetrics.from_usage(cb.usage_metadata))

    # Convert the model's response into `EndpointHints`
    name_set = {e.name for e in endpoints}
    all_hints: List[EndpointHints] = []

    for fn in res.functions:
        if fn.name not in name_set:
            _log(f"{Colors.RED}LLM returned hints for unknown endpoint {Colors.YELLOW}{fn.name}{Colors.END}, skipping…")
            continue
        converted: List[str] = []
        for hint in fn.hints:
            try:
                converted.append(hint.hint_bytes.hex())
            except Exception:
                _log(f"{Colors.RED}Failed to convert hint '{hint.hint}' for {Colors.YELLOW}{fn.name}{Colors.END}")
        all_hints.append(EndpointHints(name=fn.name, hints=converted))

    _log(f"Generated hints for {len(all_hints)}/{len(endpoints)} endpoints in batch.")

    # Print the hints
    for hint in all_hints:
        print(f"[{Colors.YELLOW}{hint.name}{Colors.END}]")
        for hint in hint.hints:
            print(f" - {hint} ({Colors.CYAN}{bytes.fromhex(hint)}{Colors.END})")

    return all_hints
