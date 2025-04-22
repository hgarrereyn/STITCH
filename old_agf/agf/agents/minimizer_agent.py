
from typing import Union, Annotated, Optional, Literal, List
from langgraph.graph import StateGraph, START, END
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langchain_core.messages import ToolMessage
from pathlib import Path
from langgraph.prebuilt import ToolNode
from ..utils.sqlitecache import SQLiteCacheFixed
from langchain.globals import set_llm_cache
from pydantic import BaseModel, Field
from typing import Annotated, List, Tuple
import operator
from ..utils.info import ProjectInfo
from ..utils import tools
from ..utils.spec import Schema, Endpoint
from ..utils.builder import try_build_external
from ..utils.colors import Colors
import tempfile
import subprocess
from ..runner.crash_repro import observe_crash
from ..build import build_command

class MinimizationAttempt(BaseModel):
    explanation: str
    new_code: str


class MinimizerAgentState(BaseModel):
    info: ProjectInfo
    model: str

    # Original code and crash report
    original_code: str
    original_crash_report: str
    original_crash_summary: str

    current_code: str
    current_crash_report: str
    did_minimize: bool = False

    # Messages
    messages: Annotated[list, operator.add] = Field(default_factory=list)

    # Number of attempts remaining
    remaining_attempts: int = 10


def test_program(code: str, info: ProjectInfo) -> Tuple[str, bool, str, str]:
    """Compile and run a test case demonstrating the bug. Returns the output of running the program."""
    # Create a temporary directory to store our files
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create the cpp file
        cpp_path = Path(tmpdir) / "test.cpp"
        with open(cpp_path, 'w') as f:
            f.write(code)

        # Get the base path without extension for the build script
        base_path = str(cpp_path.with_suffix(''))
        
        # Run the build script
        build_result = build_command(base_path, info, True)
        
        if build_result.returncode != 0:
            return f"Build failed:\n{build_result.stderr}", False, '', ''
        
        # Try to run the program under casr-gdb
        binary_path = base_path  # The build script should create this
        crash_report, crash_summary = observe_crash([binary_path])

        if crash_report is not None:
            return f"Program crashed:\n{crash_report}", True, crash_summary, crash_report
        else:
            return f"Program ran without crashing", False, '', ''


PROMPT_MINIMIZE = '''
You are an expert C/C++ developer.

Your task is to understand and minimize a crashing program.

You will be given a cpp program that crashes and a crash report.

You have access to the following tools:
- `search_ctags`: search the codebase for the given identifier (e.g. function name, type name, struct name, etc.)
- `MinimizationAttempt`: attempt a minimization of the program, will return whether the program crashed or not and the crash report

1. Read the code and crash report carefully.
2. Try to remove parts of the code and emit a `MinimizationAttempt` with your new attempt.
3. Repeat until you have a minimal reproducer.
'''.strip()


def initialize(state: MinimizerAgentState) -> dict:
    return {
        'messages': [
            {'role': 'system', 'content': PROMPT_MINIMIZE + '\n\n[CODE]\n' + state.original_code + '\n[/CODE]\n\n[REPORT]\n' + state.original_crash_report + '\n[/REPORT]'},
        ],
        'current_code': state.original_code,
        'current_crash_report': state.original_crash_report,
    }


def act(state: MinimizerAgentState) -> dict:
    llm = ChatOpenAI(model=state.model).bind_tools([
        MinimizationAttempt,
        tools.search_ctags,
        # tools.list_directory,
        # tools.read_file
    ], tool_choice='required', parallel_tool_calls=False, strict=True)
    res = llm.invoke(state.messages)

    return {
        'messages': [res],
        'remaining_attempts': state.remaining_attempts - 1
    }

def check_minimization(state: MinimizerAgentState) -> dict:
    ai_message = state.messages[-1]
    tool_call = ai_message.tool_calls[0]

    completion = MinimizationAttempt(**tool_call['args'])

    messages = []
    current_code = state.current_code
    current_crash_report = state.current_crash_report
    did_minimize = state.did_minimize
    # Validate the response
    result, crashed, crash_summary, crash_report = test_program(completion.new_code, state.info)

    if crashed:
        if crash_summary == state.original_crash_summary:
            messages.append(ToolMessage(content='Successfully minimized the program.', tool_call_id=tool_call['id']))
        else:
            messages.append(ToolMessage(content=f'The program crashed differently:\n{crash_summary}\nTry a different minimization approach.\n\n[REPORT]\n{crash_report}\n[/REPORT]', tool_call_id=tool_call['id']))
    else:
        messages.append(ToolMessage(content=result, tool_call_id=tool_call['id']))

    if crashed and crash_summary == state.original_crash_summary:
        # New crash
        if len(completion.new_code) < len(state.current_code):
            print(f'{Colors.GREEN}[+] New crash size:\n{len(completion.new_code)}{Colors.END}')
            print('-' * 60)
            print(completion.new_code)
            print('-' * 60)
            current_code = completion.new_code
            current_crash_report = crash_report
            did_minimize = True
    return {
        'messages': messages,
        'current_code': current_code,
        'current_crash_report': current_crash_report,
        'did_minimize': did_minimize,
    }


def check_state(state: MinimizerAgentState) -> Literal['bail', 'check', 'tools']:
    if state.remaining_attempts <= 0:
        return 'bail'
    ai_message = state.messages[-1]
    tool_name = ai_message.tool_calls[0]['name']
    if tool_name == 'MinimizationAttempt':
        return 'check'
    else:
        return 'tools'


def build_minimizer_agent():
    workflow = StateGraph(MinimizerAgentState)

    workflow.add_node("initialize", initialize)
    workflow.add_node("act", act)
    workflow.add_node("check_minimization", check_minimization)
    workflow.add_node('tools', ToolNode([
        tools.search_ctags,
        # tools.list_directory,
        # tools.read_file,
    ]))

    workflow.add_edge("initialize", "act")
    workflow.add_conditional_edges(
        "act",
        check_state,
        {
            "check": "check_minimization",
            "tools": "tools",
            "bail": END
        }
    )
    workflow.add_edge("check_minimization", "act")
    workflow.add_edge("tools", "act")

    workflow.set_entry_point("initialize")

    return workflow.compile()


def run_minimizer_agent(info: ProjectInfo, model: str, original_code: str) -> Optional[Tuple[str, str]]:
    agent = build_minimizer_agent()

    _, crashed, original_crash_summary, original_crash_report = test_program(original_code, info)
    if not crashed:
        print(f'{Colors.RED}[-] Original code does not crash')
        return None

    state = MinimizerAgentState(
        info=info,
        model=model,
        original_code=original_code,
        original_crash_report=original_crash_report,
        original_crash_summary=original_crash_summary,
        current_code=original_code,
        current_crash_report=original_crash_report,
        attempts_remaining=10
    )
    tools.set_global_project_info(info)
    result = agent.invoke(state)
    if result['did_minimize']:
        return result['current_code'], result['current_crash_report']
    else:
        return None


def main(args):
    info = ProjectInfo.model_validate_json(open(args.info).read())
    code, crash_report = run_minimizer_agent(info, args.model, open(args.code).read())
    print(code)
    print(crash_report)

def register(subparsers):
    parser = subparsers.add_parser('minimizer', help='Minimize a program')
    parser.add_argument('--info', type=str, required=True)
    parser.add_argument('--model', type=str, required=True)
    parser.add_argument('--code', type=str, required=True)
    parser.set_defaults(func=main)
