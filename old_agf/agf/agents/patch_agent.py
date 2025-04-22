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

from ..utils.search import get_example_usage
from ..rag.rag import RAG
from ..utils import repro
from ..utils.info import ProjectInfo
from ..utils.colors import Colors
from .patch_sequence_agent import run_patch_sequence_agent, FilterResult
from .patch_endpoint_agent import run_patch_endpoint_agent, EndpointFixAttempt


class PatchAgentAssessment(BaseModel):
    relevant_endpoints: List[str]
    relevant_code: List[str]
    explanation: str
    fix_type: Literal['code', 'filter']

class PatchAgentResult(BaseModel):
    fix: EndpointFixAttempt | FilterResult | None

    def pprint(self):
        if self.fix is None:
            print(f'{Colors.RED}No fix found{Colors.END}')
        else:
            self.fix.pprint()

class PatchAgentState(BaseModel):
    info: ProjectInfo
    model: str

    schema: str  # The fuzzer schema
    analysis: str  # Analysis of the crash
    testcase: str  # The crashing testcase
    fuzzer: str  # The fuzzer
    ex_valid: str  # Path containing valid testcases
    ex_invalid: str  # Path containing invalid testcases
    minimized_crash: str  # The minimized crash
    # Message history
    messages: Annotated[list, operator.add]

    # Number of tool uses remaining
    tool_use_remaining: int = 15

    # Result of the assessment
    res: Optional[PatchAgentAssessment] = None

    # Result of the fix
    fix: Optional[PatchAgentResult] = None


ASSESSMENT_PROMPT = """
You are an expect C/C++ programmer.

Your task is to figure out how to modify a fuzzer to avoid false positive crashes.

You will be provided with:
- A fuzzer schema - describing the API functions that the fuzzer can call
- A reproduced testcase - the testcase that is causing the crash
- An analysis of the crash - describing the false positive crash

There are two options to fix the crash:
- Fix code in one or more endpoint templates
- Implement a filter function that runs before the testcase is executed to determine if the testcase is valid

Your first task is to determine which of the two options is the correct one.

Work step-by-step:
1. Identify the relevant endpoints in the fuzzer schema.
2. Identify the relevant code inside endpoint templates (if applicable).
3. Provide an explanation.
4. Then emit one of the following fix types:
    - "code": code in one or more endpoint templates is incorrect
    - "filter": a filter function is needed to avoid false positives
""".strip()


def initialize_state(state: PatchAgentState):
    messages = []
    messages.append({
        'type': 'system',
        'content': ASSESSMENT_PROMPT
    })
    messages.append({
        'type': 'user',
        'content': f'Fuzzer schema:\n{open(state.schema).read()}'
    })
    messages.append({
        'type': 'user',
        'content': f'AI analysis of the crash:\n{open(state.analysis).read()}'
    })
    return {
        'messages': messages
    }


def run_assessment(state: PatchAgentState) -> dict:
    """Run the assessment agent to determine the cause of the crash."""
    print('[*] Running assessment')

    llm = ChatOpenAI(model=state.model)
    structured_llm = llm.with_structured_output(PatchAgentAssessment)

    res = structured_llm.invoke(state.messages)

    print(f"{Colors.YELLOW}Explanation:{Colors.END}\n{res.explanation}")
    print(f"{Colors.YELLOW}Relevant Endpoints:{Colors.END} {res.relevant_endpoints}")
    for code in res.relevant_code:
        print(f"{Colors.YELLOW}Relevant Code:{Colors.END}\n{code}")
    print(f"{Colors.YELLOW}Fix Type:{Colors.END} {res.fix_type}")

    return {
        "res": res,
        "messages": [{
            "role": "assistant",
            "content": res.model_dump_json(),
        }]
    }

def dispatch_fix(state: PatchAgentState) -> Literal['fix_endpoint', 'fix_sequence']:
    """Dispatch the fix to the appropriate agent."""
    if state.res.fix_type == 'code':
        return 'fix_endpoint'
    else:
        return 'fix_sequence'

def fix_endpoint(state: PatchAgentState) -> dict:
    """Fix the endpoint."""
    print('[*] Fixing endpoint')
    res = run_patch_endpoint_agent(
        info=state.info,
        model=state.model,
        schema=state.schema,
        analysis=state.analysis,
        testcase=state.testcase,
        minimized_crash=state.minimized_crash,
        fuzzer=state.fuzzer,
        messages=state.messages,
    )

    return {
        "fix": PatchAgentResult(fix=res),
    }

def fix_sequence(state: PatchAgentState) -> dict:
    """Fix the sequence."""
    print('[*] Fixing sequence')
    res = run_patch_sequence_agent(
        info=state.info,
        model=state.model,
        schema=state.schema,
        analysis=state.analysis,
        testcase=state.testcase,
        fuzzer=state.fuzzer,
        ex_valid=state.ex_valid,
        ex_invalid=state.ex_invalid,
    )

    return {
        "fix": PatchAgentResult(fix=res),
    }

def build_patch_agent():
    workflow = StateGraph(PatchAgentState)

    # Add nodes
    workflow.add_node('initialize_state', initialize_state)
    workflow.add_node('run_assessment', run_assessment)
    workflow.add_node('fix_endpoint', fix_endpoint)
    workflow.add_node('fix_sequence', fix_sequence)

    # Add edges
    workflow.add_edge('initialize_state', 'run_assessment')
    workflow.add_conditional_edges('run_assessment', dispatch_fix)
    workflow.add_edge('fix_endpoint', END)
    workflow.add_edge('fix_sequence', END)

    workflow.set_entry_point('fix_endpoint')
    
    return workflow.compile()


def run_patch_agent(info: ProjectInfo, model: str, schema: str, analysis: str, testcase: str, fuzzer: str, ex_valid: str, ex_invalid: str, minimized_crash: str) -> PatchAgentResult:
    """Run the patch agent to analyze and fix a bug."""
    state = PatchAgentState(
        info=info,
        model=model,
        schema=schema,
        analysis=analysis,
        testcase=testcase,
        fuzzer=fuzzer,
        messages=[],
        ex_valid=str(Path(ex_valid).resolve()),
        ex_invalid=str(Path(ex_invalid).resolve()),
        minimized_crash=str(Path(minimized_crash).resolve()),
    )
    graph = build_patch_agent()
    res = graph.invoke(state)
    return res['fix']


def main(args):
    if args.cache:
        print(f'Using SQLite cache')
        cache = SQLiteCacheFixed(database_path=".cache.db")
        set_llm_cache(cache)

    info = ProjectInfo.model_validate_json(open(args.info).read())
    res: PatchAgentResult = run_patch_agent(info, args.model, args.schema, args.analysis, args.testcase, args.fuzzer, args.ex_valid, args.ex_invalid)
    res.pprint()

def register(subparsers):
    parser = subparsers.add_parser('patch')
    parser.add_argument('--info', type=str, required=True)
    parser.add_argument('--model', type=str, required=True)
    parser.add_argument('--schema', type=str, required=True)
    parser.add_argument('--analysis', type=str, required=True)
    parser.add_argument('--testcase', type=str, required=True)
    parser.add_argument('--fuzzer', type=str, required=True)

    # Examples of valid and invalid testcases
    parser.add_argument('--ex-valid', type=str, required=True)
    parser.add_argument('--ex-invalid', type=str, required=True)

    parser.add_argument('--cache', action='store_true', help='Cache the results of the patch agent')
    parser.set_defaults(func=main)
