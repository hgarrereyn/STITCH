from typing import List, Annotated, Optional, Literal
from pydantic import BaseModel
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END
import operator
from langgraph.prebuilt import ToolNode

from ..utils import tools
from ..utils.info import ProjectInfo
from ..utils.spec import DetailedStub, EndpointStub


class GenStubState(BaseModel):
    info: ProjectInfo
    model: str
    objects: List[str]
    function: str

    # Internal
    tool_calls_remaining: int = 5
    messages: Annotated[list, operator.add] = []
    stubs: Optional[List[EndpointStub]] = None


PROMPT_STUB = '''
You are an expert C/C++ developer.

Your task is to determine the correct way(s) to invoke the function `{function}`.

You have access to the following tools:
- search_codebase: search the codebase for a string
- list_directory: list the contents of a directory
- read_file: read the contents of a file

Once you have determined the correct way(s) to invoke the function, please return a detailed stub, describing each argument (and the return value) in the function.

Specifically, for each argument (and the return value), specify the following:
- "name"
- "type" C/C++ type
- "base_type" the underlying object matching this type (must match an entry in the provided list of objects or "none" if none of the object types match)
- "dir" which can be either:
    - "new" if the argument is being created (i.e. an output of the function)
    - "del" if the argument is being destructed (use the name of the function and docs to understand if this is the case) -- e.g. functions with words like "free" or "delete"
    - "*" in all other cases
- "is_optional" true if the argument can be null, false otherwise
'''.strip()

PROMPT_STUB_COMPLETE = '''
Now that you have determined the correct way to invoke this function, please return a detailed stub.
'''.strip()


def initialize(state: GenStubState) -> dict:
    return {
        "tool_calls_remaining": 5,
        "messages": [
            {"role": "system", "content": PROMPT_STUB.format(function=state.function)},
            {"role": "user", "content": f"The list of available base_type objects is: {state.objects}"},
        ],
        "stubs": None
    }

def agent(state: GenStubState) -> dict:
    llm = ChatOpenAI(model=state.model)

    if state.tool_calls_remaining > 0:
        llm_with_tools = llm.bind_tools([
            tools.search_codebase,
            tools.list_directory,
            tools.read_file
        ])
        msg = llm_with_tools.invoke(state.messages)
        return {
            "messages": [msg],
            "tool_calls_remaining": state.tool_calls_remaining - 1
        }
    else:
        m = [{"role": "user", "content": 'You have no tool calls remaining. Please make your assessment.'}]
        msg = llm.invoke(state.messages + m)
        return {
            "messages": m + [msg],
            "tool_calls_remaining": state.tool_calls_remaining - 1
        }

def should_use_tool(state: GenStubState) -> Literal['tools', 'respond', 'done']:
    if state.tool_calls_remaining <= 0:
        # We've exhausted our tool calls
        return 'done'

    ai_message = state.messages[-1]
    if hasattr(ai_message, "tool_calls") and len(ai_message.tool_calls) > 0:
        return 'tools'
    else:
        return 'respond'

def respond_init(state: GenStubState):
    return {
        "messages": [{"role": "user", "content": PROMPT_STUB_COMPLETE}],
    }

def respond(state: GenStubState):
    print(f'[*] Worker {state.function}')
    llm = ChatOpenAI(model=state.model).with_structured_output(DetailedStub)
    
    stub: DetailedStub = llm.invoke(state.messages)
    # Validate the base_type of each argument
    err_msg = stub.validate(state.objects)
    if err_msg is not None:
        return {
            "messages": [
                {"role": "assistant", "content": stub.model_dump_json()},
                {"role": "user", "content": err_msg}
            ],
            "tool_calls_remaining": state.tool_calls_remaining - 1
        }
    else:
        stubs = stub.expand_stubs(state.function)
        return {"stubs": stubs}

def check_done(state: GenStubState) -> Literal['respond', 'done']:
    if state.tool_calls_remaining <= 0:
        return 'done'
    
    if state.stubs is not None:
        return 'done'
    else:
        return 'respond'

def build_gen_stub():
    workflow = StateGraph(GenStubState)
    workflow.add_node("initialize", initialize)
    workflow.add_node("agent", agent)
    workflow.add_node("respond_init", respond_init)
    workflow.add_node("respond", respond)
    workflow.add_node('tools', ToolNode([
        tools.search_codebase,
        tools.list_directory,
        tools.read_file
    ]))
    
    # Add edges
    workflow.add_edge('initialize', 'agent')
    workflow.add_conditional_edges('agent', should_use_tool, {
        'tools': 'tools',
        'respond': 'respond_init',
        'done': END
    })
    workflow.add_edge('tools', 'agent')
    workflow.add_edge('respond_init', 'respond')
    workflow.add_conditional_edges('respond', check_done, {
        'respond': 'respond',
        'done': END
    })
    
    workflow.set_entry_point('initialize')
    
    return workflow.compile()

def run_gen_stub(info: ProjectInfo, model: str, objects: List[str], function: str) -> List[EndpointStub]:
    state = GenStubState(info=info, model=model, objects=objects, function=function)
    graph = build_gen_stub()
    res = graph.invoke(state)
    return res['stubs']
