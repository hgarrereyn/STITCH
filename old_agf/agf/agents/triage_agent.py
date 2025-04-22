from typing import Union, Annotated, Optional, Literal, List
from pydantic import BaseModel
from langgraph.graph import StateGraph, START, END
from langchain_openai import ChatOpenAI
import operator
from langchain_core.tools import tool
from pathlib import Path
from langgraph.prebuilt import ToolNode
from ..utils.sqlitecache import SQLiteCacheFixed
from langchain.globals import set_llm_cache

from ..utils.search import get_example_usage
from ..rag.rag import RAG
from ..utils import repro
from ..utils.colors import Colors
from ..utils import tools
from ..utils.info import ProjectInfo


class TriageAgentResult(BaseModel):
    endpoints: List[str]  # List of endpoint names involved in the crash
    explanation_of_bug: str  # Explanation of the bug
    test_case: str  # Test case demonstrating the bug
    fix_suggestion: str  # Suggestion for how to fix the bug

    def pprint(self):
        print(f"\n{Colors.BOLD}Triage Analysis Result:{Colors.END}\n")
        print(f"{Colors.YELLOW}Endpoints:{Colors.END} {', '.join(self.endpoints)}")
        print(f"\n{Colors.YELLOW}Explanation:{Colors.END}\n{self.explanation_of_bug}")
        print(f"\n{Colors.YELLOW}Test Case:{Colors.END}\n{self.test_case}")
        print(f"\n{Colors.YELLOW}Fix Suggestion:{Colors.END}\n{self.fix_suggestion}")


class TriageAgentState(BaseModel):
    info: ProjectInfo
    model: str
    minimized_crash: str
    crash_report: str

    # Message history
    messages: Annotated[list, operator.add]

    # Number of tool uses remaining
    tool_use_remaining: int = 10

    # Result of the triage agent
    res: Optional[TriageAgentResult] = None

    # API filter (if not a bug)
    api_filter: Optional[str] = None


SYSTEM_PROMPT = """
You are an expert C/C++ programmer that analyzes crashes.

You will be provided a crashing test case which exercises some library API and a crash report.

The test case is a C++ program consisting of endpoint blocks, denoted by `===== START [endpoint_name] =====` and `===== END [endpoint_name] =====`.

You will need to determine if the crash is a bug in the library or a false positive (i.e. some endpoint code calls the API incorrectly or violates the API contract).

Your tasks:
1. Search the codebase and associated documentation to determine the cause of the crash.
2. Determine which endpoint(s) are involved in the crash.
3. Generate a test case demonstrating the bug.
4. Determine if the crash is a bug in the library or a false positive.

Notes:
- Do not take the crash report at face value. It may be incomplete or incorrect. Generate your own test cases to verify the crash.

You have access to the following tools:
- `test_program`: compile and run a test case demonstrating the bug
- `search_codebase`: search the codebase for a given string (i.e. "definition of Foo::bar()"), can be used to find function definitions and declarations
- `list_directory`: list the files in a given directory
- `read_file`: read up to 100 lines at a time from a given file
""".strip()

RESULT_PROMPT = """
Based on your analysis, generate a report with the following fields:
- endpoints: List of endpoint names involved in the crash
- explanation_of_bug: Explanation of the bug
- test_case: Test case demonstrating the bug
- fix_suggestion: Suggestion for how to fix the bug
""".strip()

def initialize_state(state: TriageAgentState):
    messages = []
    messages.append({
        'type': 'system',
        'content': SYSTEM_PROMPT
    })
    messages.append({
        'type': 'user',
        'content': f'Minimized crash:\n{open(state.minimized_crash).read()}'
    })
    messages.append({
        'type': 'user',
        'content': f'Crash report:\n{open(state.crash_report).read()}'
    })
    return {
        'messages': messages
    }


def triage_reasoning(state: TriageAgentState) -> dict:
    """Process the current state and generate the next action."""
    print('[*] Triage reasoning')
    
    # Initialize LLM with tools
    triage_tools = [
        tools.test_program,
        tools.search_codebase,
        tools.list_directory,
        tools.read_file
    ]
    llm = ChatOpenAI(model=state.model)
    llm_with_tools = llm.bind_tools(triage_tools)
    
    # Get next action from LLM
    msg = llm_with_tools.invoke(state.messages)
    return {
        "messages": [msg],
        "tool_use_remaining": state.tool_use_remaining - 1
    }


def triage_tool_usage(state: TriageAgentState) -> Literal['tools', 'process_complete']:
    """Determine whether to continue using tools or complete processing."""
    print('[*] Triage tool usage')

    # If no more tool uses allowed, complete processing
    if state.tool_use_remaining <= 0:
        return 'process_complete'

    # Check if last message has tool calls
    ai_message = state.messages[-1]
    if hasattr(ai_message, "tool_calls") and len(ai_message.tool_calls) > 0:
        return 'tools'
    else:
        return 'process_complete'


def process_complete(state: TriageAgentState) -> dict:
    """Generate final analysis result."""
    print('[*] Processing complete')
    
    # Initialize LLM for structured output
    llm = ChatOpenAI(model=state.model).with_structured_output(TriageAgentResult)
    
    # Add final prompt
    messages = state.messages

    # If we're bailing but the LLM tried to use tools, we need to strip the last message
    ai_message = state.messages[-1]
    if hasattr(ai_message, "tool_calls") and len(ai_message.tool_calls) > 0:
        messages = messages[:-1]

    messages.append({
        "type": "human",
        "content": RESULT_PROMPT
    })
    
    # Get structured result
    result = llm.invoke(messages)
    return {"res": result}


def build_triage_agent():
    workflow = StateGraph(TriageAgentState)
    
    # Add nodes
    workflow.add_node('initialize_state', initialize_state)
    workflow.add_node('triage_reasoning', triage_reasoning)
    workflow.add_node('triage_tool_usage', triage_tool_usage)
    workflow.add_node('process_complete', process_complete)
    workflow.add_node('tools', ToolNode([
        tools.test_program,
        tools.search_codebase,
        tools.list_directory,
        tools.read_file
    ]))
    
    # Add edges
    workflow.add_edge('initialize_state', 'triage_reasoning')
    workflow.add_edge('tools', 'triage_reasoning')
    workflow.add_conditional_edges(
        'triage_reasoning',
        triage_tool_usage,
        {
            'tools': 'tools',
            'process_complete': 'process_complete'
        }
    )
    workflow.add_edge('process_complete', END)
    
    workflow.set_entry_point('initialize_state')
    
    return workflow.compile()

 
TRIAGE_AGENT_GRAPH = build_triage_agent()


def run_triage_agent(info: ProjectInfo, model: str, minimized_crash: str, crash_report: str) -> TriageAgentResult:
    tools.set_global_build_script(info.build_script_ext)
    tools.load_global_rag(info.rag)

    state = TriageAgentState(
        info=info,
        model=model,
        minimized_crash=minimized_crash,
        crash_report=crash_report,
        messages=[],
    )
    res = TRIAGE_AGENT_GRAPH.invoke(state)
    return res['res']


def main(args):
    if args.cache:
        print(f'Using SQLite cache')
        cache = SQLiteCacheFixed(database_path=".cache.db")
        set_llm_cache(cache)

    info = ProjectInfo.model_validate_json(open(args.info).read())
    res: TriageAgentResult = run_triage_agent(info, args.model, args.minimized_crash, args.crash_report)
    
    print(f"\n{Colors.BOLD}Triage Analysis Result:{Colors.END}\n")
    print(f"{Colors.YELLOW}Is Bug:{Colors.END} {Colors.GREEN if res.is_bug else Colors.RED}{res.is_bug}{Colors.END}")
    print(f"\n{Colors.YELLOW}Explanation:{Colors.END}\n{res.explanation}")
    if res.test_case:
        print(f"\n{Colors.YELLOW}Test Case:{Colors.END}\n{res.test_case}")
    if res.how_to_ensure_api_conformity:
        print(f"\n{Colors.YELLOW}How to ensure API conformity:{Colors.END}\n{res.how_to_ensure_api_conformity}")
    if res.api_good:
        print(f"\n{Colors.YELLOW}API Good:{Colors.END}\n{res.api_good}")
    if res.api_bad:
        print(f"\n{Colors.YELLOW}API Bad:{Colors.END}\n{res.api_bad}")
    

def register(subparsers):
    parser = subparsers.add_parser('triage')
    parser.add_argument('--info', type=str, required=True)
    parser.add_argument('--model', type=str, required=True)
    parser.add_argument('--minimized_crash', type=str, required=True)
    parser.add_argument('--crash_report', type=str, required=True)

    parser.add_argument('--cache', action='store_true', help='Cache the results of the triage agent')
    parser.set_defaults(func=main)
