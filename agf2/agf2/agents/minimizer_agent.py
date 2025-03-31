
from typing import Union, Annotated, Optional, Literal, List
from langgraph.graph import StateGraph, START, END
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from pathlib import Path
from langgraph.prebuilt import ToolNode
from ..utils.sqlitecache import SQLiteCacheFixed
from langchain.globals import set_llm_cache
from pydantic import BaseModel, Field
from typing import Annotated, List, Tuple
import operator
import re
import json
from ..utils.info import ProjectInfo
from ..utils import tools
from ..utils.spec import Schema, Endpoint
from ..utils.builder import try_build_external
from ..utils.colors import Colors
import tempfile
import subprocess
from ..runner.crash_repro import observe_crash


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

    # Messages
    messages: Annotated[list, operator.add] = Field(default_factory=list)

    # Number of attempts remaining
    attempts_remaining: int = 10


def test_program(code: str, build_script: str) -> Tuple[str, bool, str, str]:
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
        build_result = subprocess.run(
            [build_script, base_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        print(f'>> build_result: {build_result.returncode} {build_result.stderr}')
        
        if build_result.returncode != 0:
            return f"Build failed:\n{build_result.stderr}", False, '', ''
        
        # Try to run the program under casr-gdb
        binary_path = base_path  # The build script should create this
        crash_report, crash_summary = observe_crash([binary_path])
        
        print(f'>> crash_report: {crash_report}')
        print(f'>> crash_summary: {crash_summary}')
        
        if crash_report is not None:
            return f"Program crashed:\n{crash_report}", True, crash_summary, crash_report
        else:
            return f"Program ran without crashing", False, '', ''


PROMPT_MINIMIZE = '''
You are an expert C/C++ developer.

You will be given a cpp program that crashes and a crash report.

Your job is to incrementally reduce the size of the program while still reproducing the crash.

Return an explanation of the changes you made to the code and the new code.

You will receive the output of the program, and whether it crashes or not along with a report of the crash.

If the program does not crash, you can try again with a different minimization.

If the program crashes, continue to attempt to minimize the program until you are confident that it is as small as possible.
'''.strip()


def initialize(state: MinimizerAgentState) -> dict:
    return {
        'messages': [
            {'role': 'system', 'content': PROMPT_MINIMIZE},
            {'role': 'user', 'content': f'// Original code:\n{state.original_code}'},
            {'role': 'user', 'content': f'// Original crash report:\n{state.original_crash_report}'},
        ],
        'current_code': state.original_code,
        'current_crash_report': state.original_crash_report
    }


def act(state: MinimizerAgentState) -> dict:
    llm = ChatOpenAI(model=state.model).with_structured_output(MinimizationAttempt)
    response: MinimizationAttempt = llm.invoke(state.messages)

    messages = [
        {'role': 'assistant', 'content': response.model_dump_json()}
    ]
    current_code = state.current_code
    current_crash_report = state.current_crash_report
    
    # Validate the reponses
    result, crashed, crash_summary, crash_report = test_program(response.new_code, state.info.build_script_ext)

    if crashed:
        if crash_summary == state.original_crash_summary:
            messages.append({'role': 'user', 'content': 'The crash is the same as the original crash. Keep going!'})
        else:
            messages.append({'role': 'user', 'content': f'// The program crashed in the wrong place:\n{crash_summary}'})
    else:
        messages.append({'role': 'user', 'content': result})

    if crashed and crash_summary == state.original_crash_summary:
        # New crash
        if len(response.new_code) < len(state.current_code):
            print(f'{Colors.GREEN}[+] New crash size:\n{len(response.new_code)}{Colors.END}')
            print('-' * 60)
            print(response.new_code)
            print('-' * 60)
            current_code = response.new_code
            current_crash_report = crash_report
    return {
        'messages': messages,
        'current_code': current_code,
        'current_crash_report': current_crash_report,
        'attempts_remaining': state.attempts_remaining - 1
    }


def check_attempts_remaining(state: MinimizerAgentState) -> Literal['act', 'done']:
    if state.attempts_remaining > 0:
        return 'act'
    else:
        return 'done'


def build_minimizer_agent():
    workflow = StateGraph(MinimizerAgentState)

    workflow.add_node("initialize", initialize)
    workflow.add_node("act", act)

    workflow.add_edge("initialize", "act")
    workflow.add_conditional_edges(
        "act",
        check_attempts_remaining,
        {
            "act": "act",
            "done": END
        }
    )
    workflow.set_entry_point("initialize")

    return workflow.compile()


def run_minimizer_agent(info: ProjectInfo, model: str, original_code: str) -> Tuple[str, str]:
    agent = build_minimizer_agent()

    _, crashed, original_crash_summary, original_crash_report = test_program(original_code, info.build_script_ext)
    if not crashed:
        print(f'{Colors.RED}[-] Original code does not crash')
        return original_code, original_crash_report

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
    result = agent.invoke(state)
    return result['current_code'], result['current_crash_report']


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
