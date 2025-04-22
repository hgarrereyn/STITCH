from typing import Literal, List, Optional, Annotated
import operator

# from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel, Field

from agf.data.project_info import ProjectInfo
from agf.data.metadata import SchemaMetadata
from agf.tools.tools import list_directory, read_file, search_ctags


PROMPT_GEN_USAGE = """
You are an expert C/C++ developer.

Your task is to determine the correct way to invoke the function `{function}`.

You should figure out:
1. The correct arguments to pass to the function
2. How these arguments map (or don't map) to the list of provided "canonical" objects
3. Any additional API restrictions when invoking the function (such as restrictions on argument ranges, etc.)

You have access to the following tools:
- `list_directory`: List the contents of a directory
- `read_file`: Read the contents of a file
- `search_ctags`: Search for a symbol in the codebase

Use the tools to understand the codebase and then invoke the StubResponse tool to respond with the correct arguments.
"""


class Arg(BaseModel):
    # Type of the argument (as specified in the function signature)
    type: str

    # Canonical type of the argument (i.e. one of the specified object types)
    canonical_type: Optional[str]

    # Name of the argument
    name: str

    # Is this argument newly created (new), being deleted (del), or just used (use)
    direction: Literal['new', 'del', 'use']


class StubResponse(BaseModel):
    # List of function arguments
    function_args: List[Arg]

    # List of API restrictions when invoking the function
    api_restrictions: List[str]


class GenUsageState(BaseModel):
    info: ProjectInfo
    meta: SchemaMetadata
    function: str
    model: str

    attempts: int = 10
    messages: Annotated[list, operator.add] = Field(default_factory=list)
    stub: Optional[StubResponse] = None


def initialize(state: GenUsageState) -> dict:
    return {
        'messages': [
            HumanMessage(content=PROMPT_GEN_USAGE.format(function=state.function))
        ]
    }


def act(state: GenUsageState) -> dict:
    llm = ChatOpenAI(model=state.model).bind_tools([
        list_directory,
        read_file,
        search_ctags,
        StubResponse
    ], strict=True, parallel_tool_calls=False)
    response = llm.invoke(state.messages)
    if response.content != '':
        print(response.content)
    return {
        'messages': [response],
        'attempts': state.attempts - 1
    }


def handle_tool_call(state: GenUsageState) -> dict:
    tool_call = state.messages[-1].tool_calls[0]
    name = tool_call['name']

    node = ToolNode(tools=[list_directory, read_file, search_ctags, StubResponse])
    res = node.invoke(state)

    stub = state.stub
    if name == 'StubResponse':
        stub = res[0]

    return {
        'messages': res['messages'],
        'stub': stub
    }


def check_state(state: GenUsageState) -> Literal['done', 'act']:
    if state.stub is not None or state.attempts <= 0:
        return 'done'
    else:
        return 'act'


def _build():
    graph = StateGraph(GenUsageState)

    graph.add_node('initialize', initialize)
    graph.add_node('act', act)
    graph.add_node('handle_tool_call', handle_tool_call)

    graph.set_entry_point('initialize')
    graph.add_edge('initialize', 'act')
    graph.add_edge('act', 'handle_tool_call')
    graph.add_conditional_edges(
        'handle_tool_call',
        check_state,
        {
            'done': END,
            'act': 'act',
        }
    )

    return graph.compile()


def gen_usage(info: ProjectInfo, meta: SchemaMetadata, model: str, function: str):
    graph = _build()
    state = GenUsageState(info=info, meta=meta, function=function, model=model)
    graph.invoke(state)


def main(args):
    info = ProjectInfo.model_validate_json(open(args.info).read())
    meta = SchemaMetadata.model_validate_json(open(args.meta).read())
    gen_usage(info, meta, args.model, args.function)


def register(subparsers):
    parser = subparsers.add_parser('gen-usage', help='Generate a usage stub for a function')
    parser.add_argument('--info', type=str, default='info.json', help='Path to the project info file')
    parser.add_argument('--meta', type=str, default='meta.json', help='Path to the schema metadata file')
    parser.add_argument('--function', type=str, required=True, help='The function to generate usage for')
    parser.add_argument('--output', type=str, default=None, help='Path to the output file')
    parser.add_argument('--model', type=str, default='gpt-4.1-nano', help='The model to use for generation')
    parser.set_defaults(func=main)
