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

from ..utils.spec import Schema, Endpoint, Stub, DetailedTemplate
from ..utils.builder import try_build
from ..utils.search import get_example_usage
from ..rag.rag import RAG
from ..utils import repro
from ..utils.colors import Colors


class EndpointFix(BaseModel):
    endpoint_name: str
    new_template: str
    explanation: str

class EndpointFixAttempt(BaseModel):
    fixes: List[EndpointFix]

    def pprint(self):
        print(f"{Colors.BOLD}Endpoint Fixes:{Colors.END}")
        for fix in self.fixes:
            print(f"{Colors.YELLOW}Endpoint:{Colors.END} {fix.endpoint_name}")
            print(f"{Colors.YELLOW}New Template:{Colors.END}\n{fix.new_template}")
            print(f"{Colors.YELLOW}Explanation:{Colors.END}\n{fix.explanation}")

class PatchEndpointAgentState(BaseModel):
    schema: str  # The fuzzer schema
    analysis: str  # Analysis of the crash
    testcase: str  # The crashing testcase
    fuzzer: str  # The fuzzer
    ex_valid: str  # Path containing valid testcases
    ex_invalid: str  # Path containing invalid testcases
    build_script: str  # The build script
    config: str  # The fuzzer config

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
Now that you have identified the relevant endpoints, rewrite the template for each erroneous endpoint to avoid the crash.

Rewrite only parts of the template that are relevant to the crash.

Return a list of fixes, each with the following fields:
- endpoint_name: The name of the endpoint to fix
- new_template: The new template for the endpoint
- explanation: An explanation of the fix

Inside each template, you have access to the `gf_rand` api:
```cpp
// Retrieve a random value for a fixed-size type T
T gf_rand::get<T>();

// Retrieve a random std::string of length len
std::string gf_rand::get_string(size_t len);
```

Notes:
- Use `gf_rand` to generate random values for any fuzzable arguments (more randomness is better), but ensure values are within the expected range.
- Use `gf_bail()` to mark an error case that should not continue.
- Do not use the `return` statement anywhere.

Do not leave any TODOs or placeholders in the code.

Tips:
- Keep the template concise and to the point
- Avoid hardcoding values, prefer to use random values when possible (e.g. use `gf_rand::get<int>()` instead of `123`)
""".strip()


def initialize_endpoint(state: PatchEndpointAgentState) -> dict:
    llm = ChatOpenAI(model="gpt-4o-mini")
    structured_llm = llm.with_structured_output(EndpointFixAttempt)

    m = [{
        'role': 'user',
        'content': FIX_ENDPOINT_PROMPT
    }]

    res = structured_llm.invoke(state.messages + m)
    return {
        "pending_fix": res,
        "valid_fix": EndpointFixAttempt(fixes=[]),
        "messages": m,
    }


def regenerate_endpoint(state: PatchEndpointAgentState) -> dict:
    """Regenerate the endpoint."""
    print('[*] Regenerating endpoint')
    llm = ChatOpenAI(model="gpt-4o-mini")
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
        res = try_build(test_endpoint, state.build_script, config=state.config)
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

def run_patch_endpoint_agent(schema: str, analysis: str, testcase: str, fuzzer: str, ex_valid: str, ex_invalid: str, build_script: str, config: str, messages: list) -> EndpointFixAttempt:
    state = PatchEndpointAgentState(
        schema=schema,
        analysis=analysis,
        testcase=testcase,
        fuzzer=fuzzer,
        ex_valid=ex_valid,
        ex_invalid=ex_invalid,
        build_script=build_script,
        config=config,
        messages=messages,
    )
    graph = build_endpoint_agent()
    res = graph.invoke(state)
    return res['fix']
