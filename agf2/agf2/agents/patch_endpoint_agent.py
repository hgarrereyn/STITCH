from typing import Union, Annotated, Optional, Literal, List, Tuple
from pydantic import BaseModel
from langgraph.graph import StateGraph, START, END
from langchain_openai import ChatOpenAI
import operator
from langchain_core.tools import tool
from pathlib import Path
from langgraph.prebuilt import ToolNode
from ..utils.sqlitecache import SQLiteCacheFixed
from langchain.globals import set_llm_cache
import subprocess
import tempfile
import random

from ..utils.spec import Schema, Endpoint
from ..utils.builder import try_build, try_build_external
from ..utils.search import get_example_usage
from ..rag.rag import RAG
from ..utils import repro
from ..utils.colors import Colors
from ..utils.info import ProjectInfo


class EndpointFix(BaseModel):
    endpoint_name: str
    explanation: str
    new_template: str

class EndpointFixAttempt(BaseModel):
    fixes: List[EndpointFix]

    def pprint(self):
        print(f"{Colors.BOLD}Endpoint Fixes:{Colors.END}")
        for fix in self.fixes:
            print(f"{Colors.YELLOW}Endpoint:{Colors.END} {fix.endpoint_name}")
            print(f"{Colors.YELLOW}New Template:{Colors.END}\n{fix.new_template}")
            print(f"{Colors.YELLOW}Explanation:{Colors.END}\n{fix.explanation}")

class PatchEndpointAgentState(BaseModel):
    info: ProjectInfo
    model: str
    schema: str  # The fuzzer schema
    analysis: str  # Analysis of the crash
    testcase: str  # The crashing testcase
    fuzzer: str  # The fuzzer
    minimized_crash: str  # The minimized crash

    # Message history
    messages: Annotated[list, operator.add]

    # Output
    fix: Optional[EndpointFixAttempt] = None

    # Internal
    valid_fix: Optional[EndpointFixAttempt] = None
    pending_fix: Optional[EndpointFixAttempt] = None
    has_errors: bool = False
    fix_attempts: int = 5


FIX_ENDPOINT_PROMPT = """
You are an expert C/C++ programmer.

You are given a crashing test case consisting of instantiations of endpoint blocks, denoted by `===== START [endpoint_name] =====` and `===== END [endpoint_name] =====`.

You are also given an analysis of why the test case is crashing.

Your task is to rewrite the template for each erroneous endpoint to avoid the crash.

Rewrite only parts of the template that are relevant to the crash.

Return a list of fixes, each with the following fields:
- endpoint_name: The name of the endpoint to fix
- explanation: An explanation of the fix
- new_template: The new template for the endpoint

Your function will be used in a fuzzing environment, use the following macros to generate random values for any fuzzable arguments:
```cpp
// Sets x to a random fixed-size value of type T
T x = FUZZ_PARAM(T);

// Sets x to a random const char * of length N (N must be a compile-time constant)
const char *x = FUZZ_PARAM_STR(N);

// Sets filename to a temporary file filled with N bytes of fuzzable data (N must be a compile-time constant)
const char *filename = FUZZ_PARAM_FILE(N);

// Marks an error case that should not continue
FUZZ_BAIL();
```

Important notes:
- Do not throw exceptions or emit `return` statements. Instead, use `FUZZ_BAIL()` to mark an error case that should not continue (i.e. if the function returns an error code).
- Use `gf_malloc`/`gf_free` to manage memory (if not using the library API).
- Do not leave any TODOs or placeholders in the code.
- Ensure that you use all provided arguments in the body of the function.
- Avoid referencing struct fields which are hidden by the library API.
- If the function requires a filename, use `FUZZ_PARAM_FILE(N)` to get a random filename.

Do not leave any TODOs or placeholders in the code.

Tips:
- Keep the template concise and to the point
- Avoid hardcoding values, prefer to use random values when possible (e.g. use `FUZZ_PARAM(int)` instead of `123`)
""".strip()


def initialize_endpoint(state: PatchEndpointAgentState) -> dict:
    llm = ChatOpenAI(model=state.model)
    structured_llm = llm.with_structured_output(EndpointFixAttempt)

    m = [
        {
            'role': 'system',
            'content': FIX_ENDPOINT_PROMPT
        },
        {
            'role': 'user',
            'content': f"Analysis of the crash:\n{open(state.analysis).read()}"
        },
        {
            'role': 'user',
            'content': f"Crashing test case:\n{open(state.minimized_crash).read()}"
        },
        {
            'role': 'user',
            'content': f"Endpoint specifications:\n{open(state.schema).read()}"
        },
    ]

    res = structured_llm.invoke(state.messages + m)
    return {
        "pending_fix": res,
        "valid_fix": EndpointFixAttempt(fixes=[]),
        "messages": m,
    }


def regenerate_endpoint(state: PatchEndpointAgentState) -> dict:
    """Regenerate the endpoint."""
    print('[*] Regenerating endpoint')
    llm = ChatOpenAI(model=state.model)
    structured_llm = llm.with_structured_output(EndpointFixAttempt)

    res = structured_llm.invoke(state.messages)
    return {
        "pending_fix": res,
        "fix_attempts": state.fix_attempts - 1,
    }


def validate_endpoint(state: PatchEndpointAgentState) -> dict:
    """Validate the endpoint."""
    print('[*] Validating endpoint')

    schema = Schema.model_validate_json(open(state.schema).read())

    valid_fix = state.valid_fix
    errors = []

    # Try to build each pending fix
    for fix in state.pending_fix.fixes:
        # If LLM incorrectly regenerated one we've already seen, skip it
        valid_names = [f.endpoint_name for f in valid_fix.fixes]
        if fix.endpoint_name in valid_names:
            print(f'{Colors.YELLOW}Skipping endpoint {fix.endpoint_name} as it has already been fixed{Colors.END}')
            continue

        # Find matching endpoint in schema
        endpoint = next((e for e in schema.endpoints if e.name == fix.endpoint_name), None)
        if endpoint is None:
            errors.append((fix.endpoint_name, f"Endpoint {fix.endpoint_name} not found in schema"))
            continue

        # Try to build
        test_endpoint = Endpoint(
            name=endpoint.name,
            variables=endpoint.variables,
            template=fix.new_template,
        )
        # res = try_build(test_endpoint, state.info.build_script, config=state.info.config)
        res = try_build_external(test_endpoint, state.info.build_script_ext, state.info.config)

        if res is None:
            print(f'{Colors.GREEN}Successfully built endpoint {fix.endpoint_name}{Colors.END}')
            # Copy the fix to valid_fix
            valid_fix.fixes.append(fix)
        else:
            print(f'{Colors.RED}Failed to build endpoint {fix.endpoint_name}: {res.error}{Colors.END}')
            errors.append((fix.endpoint_name, res.error))

    if len(errors) > 0:
        err_msg = 'Regenerate the following fixes to fix errors:\n'
        for endpoint_name, error in errors:
            err_msg += '----------------------------------------\n'
            err_msg += f'Error for [{endpoint_name}]:\n{error}\n'
            
        return {
            "has_errors": True,
            "valid_fix": valid_fix,
            "messages": [{"role": "user", "content": err_msg}],
        }
    else:
        return {
            "has_errors": False,
            "valid_fix": valid_fix,
        }


def check_regenerate(state: PatchEndpointAgentState) -> Literal['regenerate_endpoint', 'finalize_endpoint']:
    if state.fix_attempts <= 0:
        return 'finalize_endpoint'

    if state.has_errors:
        return 'regenerate_endpoint'
    else:
        return 'finalize_endpoint'


def finalize_endpoint(state: PatchEndpointAgentState) -> dict:
    print('[*] Finalizing endpoint')
    state.valid_fix.pprint()
    if len(state.valid_fix.fixes) == 0:
        return {
            "fix": None,
        }
    else:
        return {
            "fix": state.valid_fix,
        }


def build_endpoint_agent():
    workflow = StateGraph(PatchEndpointAgentState)

    # Add nodes
    workflow.add_node('initialize_endpoint', initialize_endpoint)
    workflow.add_node('regenerate_endpoint', regenerate_endpoint)
    workflow.add_node('validate_endpoint', validate_endpoint)
    workflow.add_node('finalize_endpoint', finalize_endpoint)

    # Add edges
    workflow.add_edge('initialize_endpoint', 'validate_endpoint')
    workflow.add_edge('regenerate_endpoint', 'validate_endpoint')
    workflow.add_conditional_edges('validate_endpoint', check_regenerate)
    workflow.add_edge('finalize_endpoint', END)

    workflow.set_entry_point('initialize_endpoint')
    
    return workflow.compile()

def run_patch_endpoint_agent(info: ProjectInfo, model: str, schema: str, analysis: str, testcase: str, fuzzer: str, minimized_crash: str, messages: list) -> EndpointFixAttempt:
    state = PatchEndpointAgentState(
        info=info,
        model=model,
        schema=schema,
        analysis=analysis,
        testcase=testcase,
        minimized_crash=minimized_crash,
        fuzzer=fuzzer,
        messages=messages,
    )
    graph = build_endpoint_agent()
    res = graph.invoke(state)
    return res['fix']
