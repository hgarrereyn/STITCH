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

from ..utils.spec import Endpoint, Stub, DetailedTemplate
from ..utils.builder import try_build
from ..utils.search import get_example_usage
from ..rag.rag import RAG
from ..utils import repro
from ..utils.colors import Colors


class TriageAgentResult(BaseModel):
    is_bug: bool  # True if this is a bug in the library, False if it's API misuse
    explanation: str  # Explanation of the analysis
    how_to_ensure_api_conformity: Optional[str] = None  # How to ensure API conformity if is_bug is False
    api_good: Optional[str] = None  # API usage that is good
    api_bad: Optional[str] = None  # API usage that is bad
    test_case: Optional[str] = None  # Test case demonstrating the bug if is_bug is True

    def pprint(self):
        print(f"\n{Colors.BOLD}Triage Analysis Result:{Colors.END}\n")
        print(f"{Colors.YELLOW}Is Bug:{Colors.END} {Colors.GREEN if self.is_bug else Colors.RED}{self.is_bug}{Colors.END}")
        print(f"\n{Colors.YELLOW}Explanation:{Colors.END}\n{self.explanation}")
        if self.test_case:
            print(f"\n{Colors.YELLOW}Test Case:{Colors.END}\n{self.test_case}")
        if self.how_to_ensure_api_conformity:
            print(f"\n{Colors.YELLOW}How to ensure API conformity:{Colors.END}\n{self.how_to_ensure_api_conformity}")
        if self.api_good:
            print(f"\n{Colors.YELLOW}API Good:{Colors.END}\n{self.api_good}")
        if self.api_bad:
            print(f"\n{Colors.YELLOW}API Bad:{Colors.END}\n{self.api_bad}")


class TriageAgentState(BaseModel):
    minimized_crash: str
    crash_report: str
    codebase: str
    rag: str
    build_script: str

    # Message history
    messages: Annotated[list, operator.add]

    # Number of tool uses remaining
    tool_use_remaining: int = 10

    # Result of the triage agent
    res: Optional[TriageAgentResult] = None

    # API filter (if not a bug)
    api_filter: Optional[str] = None


GLOBAL_RAG = None
GLOBAL_BUILD_SCRIPT = None
GLOBAL_RAG_OBJ = None  # New global variable to store the loaded RAG object


@tool
def test_program(code: str) -> str:
    """Compile and run a test case demonstrating the bug. Returns the output of running the program."""
    print(f'>> test_program:\n{code}')
    return repro.test_program(code, GLOBAL_BUILD_SCRIPT)


@tool
def search_codebase(query: str) -> List[str]:
    """Search the codebase for a given string. Returns matching lines with file and line number."""
    print(f'>> search_codebase: {query}')
    # Use the globally loaded RAG object instead of loading it each time
    global GLOBAL_RAG_OBJ
    
    # Perform the query
    results = GLOBAL_RAG_OBJ.query(query, top_n=5)
    
    # Format results
    output = []
    output.append(f"\nTop {len(results)} matches for query: {query}\n")
    
    for i, (chunk, similarity) in enumerate(results, 1):
        result_text = []
        result_text.append(f"Match {i} (similarity: {similarity:.3f}):")
        result_text.append(f"File: {chunk.filename}")
        result_text.append(f"Location: {chunk.start_point} - {chunk.end_point}")
        result_text.append(f"Content:")
        result_text.append("-" * 80)
        result_text.append(chunk.text.encode('utf-8', errors='ignore').decode('utf-8'))
        result_text.append("-" * 80)
        result_text.append("")
        output.append("\n".join(result_text))
        
    return output


@tool
def list_directory(path: str) -> List[str]:
    """List the contents of a directory in the codebase."""
    print(f'>> list_directory: {path}')
    try:
        p = Path(path)
        return [str(x.relative_to(path)).encode('utf-8', errors='ignore').decode('utf-8') for x in p.glob("*")]
    except Exception as e:
        return [f"Error listing directory: {str(e)}"]


@tool
def read_file(path: str, start_line: Optional[int] = None) -> str:
    """Read the contents of a file in the codebase."""
    print(f'>> read_file: {path} {start_line}')
    try:
        with open(path, 'r') as f:
            lines = f.readlines()
            if start_line is not None:
                lines = lines[start_line:]
            lines = lines[:100]
            return ("\n".join(lines)).encode('utf-8', errors='ignore').decode('utf-8')
    except Exception as e:
        return f"Error reading file: {str(e)}"


SYSTEM_PROMPT = """
You are an expert C/C++ programmer that analyzes crashes.

You will be provided a crashing test case which exercises some library API and a crash report.

You will need to determine if the crash is a bug in the library or a false positive (i.e. a misuse of the API).

Search the codebase and associated documentation to determine the cause of the crash.

Once you think you have a good understanding, use the `test_program` tool to compile and run a test case demonstrating the bug. Make sure it is hitting the same bug as the original report.

Do not take the crash report at face value. It may be incomplete or incorrect. Generate your own test cases to verify the crash.

You have access to the following tools:
- `test_program`: compile and run a test case demonstrating the bug
- `search_codebase`: search the codebase for a given string (i.e. "definition of Foo::bar()"), can be used to find function definitions and declarations
- `list_directory`: list the files in a given directory
- `read_file`: read up to 100 lines at a time from a given file
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
    tools = [test_program, search_codebase, list_directory, read_file]
    llm = ChatOpenAI(model="gpt-4o-mini")
    llm_with_tools = llm.bind_tools(tools)
    
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
    llm = ChatOpenAI(model="gpt-4o-mini").with_structured_output(TriageAgentResult)
    
    # Add final prompt
    messages = state.messages

    # If we're bailing but the LLM tried to use tools, we need to strip the last message
    ai_message = state.messages[-1]
    if hasattr(ai_message, "tool_calls") and len(ai_message.tool_calls) > 0:
        messages = messages[:-1]

    messages.append({
        "type": "human",
        "content": "Based on your analysis, please determine if this is a bug in the library or API misuse. Provide your conclusion and explanation. If it is not a bug, provide a suggestion for how to ensure API conformity (e.g. a general rule for how to use the API correctly and avoid this type of crash) and provide a positive and negative pseudo-code example of how to use the API correctly (api_good and api_bad)."
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
    workflow.add_node('tools', ToolNode([test_program, search_codebase, list_directory, read_file]))
    
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


def run_triage_agent(minimized_crash: str, crash_report: str, codebase: str, rag: str, build_script: str) -> TriageAgentResult:
    global GLOBAL_RAG
    GLOBAL_RAG = rag
    global GLOBAL_BUILD_SCRIPT
    GLOBAL_BUILD_SCRIPT = build_script
    
    # Load the RAG object once at the start
    global GLOBAL_RAG_OBJ
    print(f'Loading RAG object from {rag}')
    GLOBAL_RAG_OBJ = RAG.load(Path(rag))

    state = TriageAgentState(
        minimized_crash=minimized_crash,
        crash_report=crash_report,
        codebase=codebase,
        rag=rag,
        build_script=build_script,
        messages=[],
    )
    res = TRIAGE_AGENT_GRAPH.invoke(state)
    return res['res']


def main(args):
    if args.cache:
        print(f'Using SQLite cache')
        cache = SQLiteCacheFixed(database_path=".cache.db")
        set_llm_cache(cache)

    res: TriageAgentResult = run_triage_agent(args.minimized_crash, args.crash_report, args.codebase, args.rag, args.build_script)
    
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
    parser.add_argument('--minimized_crash', type=str, required=True)
    parser.add_argument('--crash_report', type=str, required=True)
    parser.add_argument('--codebase', type=str, required=True)
    parser.add_argument('--rag', type=str, required=True)
    parser.add_argument('--build_script', type=str, required=True)

    parser.add_argument('--cache', action='store_true', help='Cache the results of the triage agent')
    parser.set_defaults(func=main)
