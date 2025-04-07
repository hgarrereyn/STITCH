import operator
import re
from typing import Annotated, List, Dict, Any, Literal, Optional
from pathlib import Path
from pydantic import BaseModel, Field
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph
from langgraph.prebuilt import ToolNode


class AutoconfState(BaseModel):
    # The selected codebase
    codebase: str

    # Message history
    messages: Annotated[list, operator.add]

    # Number of tool uses remaining
    tool_use_remaining: int = 10

    # Public headers to process
    public_headers: Annotated[List[str], operator.add] = Field(default_factory=list)


class HeadersOutput(BaseModel):
    public_headers: List[str]


# Tool definitions
@tool
def list_directory(path: str) -> List[str]:
    """List the contents of a directory in the codebase."""
    try:
        p = Path(path)
        return [str(x.relative_to(path)) for x in p.glob("*")]
    except Exception as e:
        return f"Error listing directory: {str(e)}"

@tool
def read_file(path: str, start_line: Optional[int] = None) -> str:
    """Read the contents of a file in the codebase."""
    try:
        with open(path, 'r') as f:
            lines = f.readlines()
            if start_line is not None:
                lines = lines[start_line:]
            lines = lines[:100]
            return "\n".join(lines)
    except Exception as e:
        return f"Error reading file: {str(e)}"
    
@tool
def find_matching_regex(path: str, pattern: str) -> List[str]:
    """Find header files in the codebase whose contents match a regex pattern."""
    try:
        pattern = re.compile(pattern)
        results = []
        for file in Path(path).glob("**/*.h"):
            if file.is_file():
                with open(file, 'rb') as f:
                    if pattern.search(f.read().decode('latin-1')):
                        results.append(str(file))
        return results
    except Exception as e:
        return f"Error performing grep search: {str(e)}"


SYSTEM_PROMPT = """You are an expert C/C++ developer tasked with finding all public API headers in a codebase.
Your goal is to identify header files that contain public API declarations.

Look for:
- Header files (*.h, *.hpp)
- Files containing public function declarations
- Files with documented APIs
- Files referenced in build systems or documentation (e.g. CMakeLists.txt, README.md)

Avoid:
- Internal/private headers
- Implementation files
- Test headers

You have access to these tools:
- list_directory: List contents of a directory
- read_file: Read contents of a file (maximum 100 lines at a time, specify start_line to read from a specific line)
- find_matching_regex: Find header files whose contents match a regex pattern (useful for finding API declarations)

Examine the codebase systematically and report back headers that look like public APIs."""


def main(args):
    tools = [list_directory, read_file, find_matching_regex]
    llm = ChatOpenAI(model="o3-mini")
    llm_with_tools = llm.bind_tools(tools)
    llm_structured = llm.with_structured_output(HeadersOutput)

    def process(state: AutoconfState) -> Dict[str, Any]:
        print('processing', state.messages)
        msg = llm_with_tools.invoke(state.messages)
        return {"messages": [msg]}

    def process_tool(state: AutoconfState) -> Literal['tools', 'process_complete']:
        state.tool_use_remaining -= 1
        if state.tool_use_remaining <= 0:
            return 'process_complete'

        ai_message = state.messages[-1]
        if hasattr(ai_message, "tool_calls") and len(ai_message.tool_calls) > 0:
            return 'tools'
        else:
            return 'process_complete'

    tool_node = ToolNode(tools)

    def process_complete(state: AutoconfState) -> Dict[str, Any]:
        print('process_complete', state.messages)
        messages = state.messages
        # messages.append(HumanMessage(content="Now that you've found the public headers, please list them all."))
        messages.append({
            "type": "human",
            "content": "Now that you've found the public headers, please list them all."
        })
        headers = llm_structured.invoke(messages)
        return {"public_headers": headers.public_headers}


    # Create the graph
    workflow = StateGraph(AutoconfState)
    
    workflow.add_node("process", process)
    workflow.add_node("tools", tool_node)
    workflow.add_node("process_complete", process_complete)

    workflow.add_edge("tools", "process")
    workflow.add_conditional_edges('process', process_tool)

    workflow.set_entry_point("process")
    
    # Compile the graph
    app = workflow.compile()
    
    # Run the graph
    state = AutoconfState(codebase=args.codebase, messages=[
        # SystemMessage(SYSTEM_PROMPT),
        # HumanMessage(content=f"Examine the codebase at {args.codebase} and find public API headers.")
        {
            "type": "system",
            "content": SYSTEM_PROMPT
        },
        {
            "type": "human",
            "content": f"Examine the codebase at {args.codebase} and find public API headers."
        }
    ], headers=[])
    result = app.invoke(state.model_dump())
    
    print(f"Found public headers: {result}")


def register(subparsers):
    parser = subparsers.add_parser('autoconf', help='Auto-configure a codebase')
    parser.set_defaults(func=main)
    parser.add_argument('codebase', type=str, help='The codebase to process')
