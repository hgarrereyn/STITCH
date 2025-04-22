
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


class EndpointFix(BaseModel):
    endpoint_name: str
    explanation: str
    new_template: str


class EndpointFixAttempt(BaseModel):
    fixes: List[EndpointFix]


class UnableToFix(BaseModel):
    reason: str


class FixAttempt(BaseModel):
    fix: EndpointFixAttempt | UnableToFix


class CrashAnalysisResult(BaseModel):
    # Cpp file with the minimized crash
    minimized_crash: str

    # Crash report
    crash_report: str

    # Analysis conversation
    messages: list

    # Summary of the analysis
    summary: str | None = None

    # Result of the analysis
    fix: FixAttempt | None = None

    def pprint(self):
        print(f'{Colors.YELLOW}Crash analysis:{Colors.END}')
        if self.summary is not None:
            print(f'  {Colors.YELLOW}Summary:{Colors.END}')
            print(f'    {self.summary}{Colors.END}')

        if self.fix is not None:
            if isinstance(self.fix.fix, EndpointFixAttempt):
                for fix in self.fix.fix.fixes:
                    print(f'  {Colors.GREEN}Endpoint: {fix.endpoint_name}{Colors.END}')
                    print(f'    {Colors.GREEN}Explanation: {fix.explanation}{Colors.END}')
                    print(f'    {Colors.GREEN}New template: {fix.new_template}{Colors.END}')
            elif isinstance(self.fix.fix, UnableToFix):
                print(f'  {Colors.RED}Unable to fix{Colors.END}')
                print(f'    {Colors.RED}Reason: {self.fix.reason}{Colors.END}')
        else:
            print(f'  {Colors.RED}No fix attempted{Colors.END}')


class CrashAnalysisAgentState(BaseModel):
    info: ProjectInfo
    model: str

    # Cpp file with the minimized crash
    minimized_crash: str

    # Crash report
    crash_report: str

    # Path to current fuzzer schema
    current_schema: str

    # Analysis history
    analysis_history: List[CrashAnalysisResult]

    # Messages
    messages: Annotated[list, operator.add] = Field(default_factory=list)

    # Number of tool uses remaining
    tool_use_remaining: int = 10
    fix_attempts_remaining: int = 5

    # Result of the analysis
    fix_attempt: FixAttempt | None = None
    validated_endpoint_fixes: List[EndpointFix] = Field(default_factory=list)
    fix_errors: bool = False
    result: FixAttempt | None = None
    summary: str | None = None


PROMPT_SYSTEM = """
You are an expert C/C++ developer tasked with analyzing and fixing a crash in a program.

You will be given a minimized crash file which comes from a fuzzer.

The crash file consists of instantiations of code block templates, denoted by `===== START [block_name] =====` and `===== END [block_name] =====`.

You will also be given a crash report which contains the stack trace and other information about the crash.

Your task is to determine why the crash is happening and fix the fuzzer code block templates to avoid the crash in the future.

For example, if the crash happens because a code block uses the program API incorrectly, you should fix the code block template to use the API correctly.
""".strip()


PROMPT_FIX_REQUEST = """
Now propose a fix for the crash rewriting the code block templates, and explain why you chose the fix you did.

Your code block templates will be used in a fuzzing environment, so when possible, use the following macros to generate random values for any fuzzable arguments:
```cpp
// Sets x to a random fixed-size value of type T
T x = FUZZ_PARAM(T);

// Sets x to a random const char * of length len
const char *x = FUZZ_PARAM_STR(len);

// Sets filename to the path of a file filled with fuzzable data
const char *filename = FUZZ_FILE();

// Marks an error case that should not continue
FUZZ_BAIL();
```

Important notes:
- Do not throw exceptions or emit `return` statements. Instead, use `FUZZ_BAIL()` to mark an error case that should not continue (i.e. if the function returns an error code).
- Use `gf_malloc`/`gf_free` to manage memory (if not using the library API).
- Do not leave any TODOs or placeholders in the code.
- Ensure that you use all provided arguments in the body of the function.
- Avoid referencing struct fields which are hidden by the library API.
- If the function requires a filename, use `FUZZ_FILE()` to get a random filename.

Do not leave any TODOs or placeholders in the code.

If you are unable to fix the crash, explain why you cannot fix the crash.
""".strip()


def _extract_relevant_schema(schema: str, crash_file: str) -> List[Endpoint]:
    # Extract the code block templates from the crash file
    code_blocks = set()
    # use regex to find all the code blocks
    pattern = re.compile(r"===== START \[([^\]]+)\] =====", re.DOTALL)
    matches = pattern.findall(open(crash_file).read())
    for match in matches:
        code_blocks.add(match)

    schema: Schema = Schema.model_validate_json(open(schema).read())
    relevant = []
    for endpoint in schema.endpoints:
        if endpoint.name in code_blocks:
            relevant.append(endpoint.model_dump())

    return relevant

def initialize(state: CrashAnalysisAgentState) -> dict:
    messages = []
    messages.append({
        "type": "system",
        "content": PROMPT_SYSTEM
    })
    messages.append({
        "type": "user",
        "content": f"// Minimized crash file:\n{open(state.minimized_crash).read()}"
    })
    messages.append({
        "type": "user",
        "content": f"// Crash report:\n{open(state.crash_report).read()}"
    })
    
    relevant_schema = _extract_relevant_schema(state.current_schema, state.minimized_crash)
    messages.append({
        "type": "user",
        "content": f"// Endpoint templates that are relevant to the crash:\n{json.dumps(relevant_schema, indent=2)}"
    })

    summaries = [h.summary for h in state.analysis_history if h.summary is not None]
    if len(summaries) > 0:
        summary_str = "\n\n---\n\n".join(summaries)
        messages.append({
            "type": "user",
            "content": f"This bug has been attempted to be fixed before, but not patched correctly. Here are the previous attempts. Do not repeat the same mistakes.\n\n{summary_str}"
        })

    return {"messages": messages}

def analyze(state: CrashAnalysisAgentState) -> CrashAnalysisResult:
    llm_with_tools = ChatOpenAI(model=state.model).bind_tools([
        tools.test_program,
        tools.search_codebase,
        tools.list_directory,
        tools.read_file
    ])
    res = llm_with_tools.invoke(state.messages)
    return {
        "messages": [res],
        "tool_use_remaining": state.tool_use_remaining - 1
    }

def check_tool_usage(state: CrashAnalysisAgentState) -> Literal['tools', 'fail', 'complete']:
    """Determine whether to continue using tools or complete processing."""
    print('[*] Crash analysis tool usage')

    # If no more tool uses allowed, complete processing
    if state.tool_use_remaining <= 0:
        return 'fail'

    # Check if last message has tool calls
    ai_message = state.messages[-1]
    if hasattr(ai_message, "tool_calls") and len(ai_message.tool_calls) > 0:
        return 'tools'
    else:
        return 'complete'

def initialize_fix(state: CrashAnalysisAgentState) -> dict:
    return {
        "messages": [
            {
                "type": "user",
                "content": PROMPT_FIX_REQUEST
            }
        ]
    }

def attempt_fix(state: CrashAnalysisAgentState) -> dict:
    print('[*] Attempting fix')
    llm = ChatOpenAI(model=state.model).with_structured_output(FixAttempt)
    res: FixAttempt = llm.invoke(state.messages)
    return {
        "messages": [{
            "type": "assistant",
            "content": res.model_dump_json(indent=2)
        }],
        "fix_attempt": res
    }

def validate_fix(state: CrashAnalysisAgentState) -> dict:
    print('[*] Validating fix')
    current_schema: Schema = Schema.model_validate_json(open(state.current_schema).read())

    validated_fixes = state.validated_endpoint_fixes

    fix = state.fix_attempt.fix
    if isinstance(fix, UnableToFix):
        pass
    elif isinstance(fix, EndpointFixAttempt):
        # Validate each proposed endpoint fix
        endpoint_failures = []
        for endpoint_fix in fix.fixes:
            # Skip if the endpoint fix is already validated
            seen = any(e.endpoint_name == endpoint_fix.endpoint_name for e in validated_fixes)
            if seen:
                continue

            # Find matching endpoint in schema
            endpoint = next((e for e in current_schema.endpoints if e.name == endpoint_fix.endpoint_name), None)
            if endpoint is None:
                endpoint_failures.append(f"Endpoint {endpoint_fix.endpoint_name} not found in schema")
                continue

            # Test the endpoint fix
            test_endpoint = Endpoint(
                name=endpoint.name,
                variables=endpoint.variables,
                template=endpoint_fix.new_template,
            )
            res = try_build_external(test_endpoint, state.info.build_script_ext, state.info.config)
            if res is None:
                print(f'{Colors.GREEN}Successfully built endpoint {endpoint_fix.endpoint_name}{Colors.END}')
                validated_fixes.append(endpoint_fix)
            else:
                print(f'{Colors.RED}Failed to build endpoint {endpoint_fix.endpoint_name}: {res.error}{Colors.END}')
                endpoint_failures.append(f"Failed to build endpoint {endpoint_fix.endpoint_name}: {res.error}")

    if len(endpoint_failures) > 0:
        return {
            "messages": [{
                "type": "user",
                "content": f"There were errors when building the following endpoint fixes: {endpoint_failures}"
            }],
            "fix_errors": True,
            "fix_attempts_remaining": state.fix_attempts_remaining - 1,
            "validated_endpoint_fixes": validated_fixes,
        }
    else:
        return {
            "fix_errors": False,
            "validated_endpoint_fixes": validated_fixes,
        }
    
def check_fix_errors(state: CrashAnalysisAgentState) -> Literal['fix_errors', 'complete']:
    if state.fix_attempts_remaining <= 0:
        return 'complete'
    
    if state.fix_errors:
        return 'fix_errors'
    else:
        return 'complete'

def complete_fix(state: CrashAnalysisAgentState) -> dict:
    print('[*] Completing fix')

    # If we have some fixes, return them
    if len(state.validated_endpoint_fixes) > 0:
        # Get llm to summarize findings from this analysis
        llm = ChatOpenAI(model=state.model)
        res = llm.invoke(state.messages + [
            {
                "type": "user",
                "content": "Summarize all the steps you took during this analysis, your findings, and the reason you chose the endpoint fixes you did."
            }
        ])

        summary = res.content
        return {
            "result": FixAttempt(
                fix=EndpointFixAttempt(
                    fixes=state.validated_endpoint_fixes
                )
            ),
            "summary": summary
        }
    elif isinstance(state.fix_attempt.fix, UnableToFix):
        return {
            "result": state.fix_attempt,
        }
    else:
        pass


def build_crash_analysis_agent():
    workflow = StateGraph(CrashAnalysisAgentState)

    workflow.add_node("initialize", initialize)
    workflow.add_node("analyze", analyze)
    workflow.add_node("initialize_fix", initialize_fix)
    workflow.add_node("attempt_fix", attempt_fix)
    workflow.add_node("validate_fix", validate_fix)
    workflow.add_node("complete_fix", complete_fix)
    workflow.add_node('tools', ToolNode([
        tools.test_program,
        tools.search_codebase,
        tools.list_directory,
        tools.read_file
    ]))

    workflow.add_edge("initialize", "analyze")
    workflow.add_conditional_edges(
        "analyze",
        check_tool_usage,
        {
            "tools": "tools",
            "complete": "initialize_fix",
            "fail": "complete_fix"
        }
    )
    workflow.add_edge("tools", "analyze")
    workflow.add_edge("initialize_fix", "attempt_fix")
    workflow.add_edge("attempt_fix", "validate_fix")
    workflow.add_conditional_edges(
        "validate_fix",
        check_fix_errors,
        {
            "fix_errors": "attempt_fix",
            "complete": "complete_fix"
        }
    )
    workflow.add_edge("complete_fix", END)
    workflow.set_entry_point("initialize")

    return workflow.compile()


def run_crash_analysis_agent(info: ProjectInfo, model: str, minimized_crash: str, crash_report: str, current_schema: str, analysis_history: List[CrashAnalysisResult]) -> CrashAnalysisResult:
    tools.set_global_build_script(info.build_script_ext)
    tools.load_global_rag(info.rag)
    
    state = CrashAnalysisAgentState(
        info=info,
        model=model,
        minimized_crash=minimized_crash,
        crash_report=crash_report,
        current_schema=current_schema,
        analysis_history=analysis_history
    )

    agent = build_crash_analysis_agent()
    result = agent.invoke(state)
    res = CrashAnalysisResult(
        minimized_crash=minimized_crash,
        crash_report=crash_report,
        messages=result['messages'],
        fix=result['result'],
        summary=result['summary']
    )
    return res
