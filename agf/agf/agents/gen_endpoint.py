
from typing import Union, Annotated, Optional, Literal, List
import operator
from pydantic import BaseModel
from langgraph.graph import StateGraph, START, END
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import ToolNode
from langchain_core.runnables.config import RunnableConfig
from langchain_core.messages import HumanMessage, ToolMessage, SystemMessage
from ..utils.spec import Endpoint, EndpointStub, EndpointFunctionCompletion
from ..utils.builder import try_build_external
from ..utils import tools
from ..utils.info import ProjectInfo
from ..utils.tools import set_global_project_info

class GenEndpointState(BaseModel):
    info: ProjectInfo
    model: str
    stub: EndpointStub
    type: Literal['function', 'construct', 'destruct']

    # generated
    remaining_attempts: int = 8
    completion: Optional[EndpointFunctionCompletion] = None
    messages: Annotated[list, operator.add] = []
    res: Optional['GenEndpointResult'] = None


class GenEndpointSuccess(BaseModel):
    endpoint: Endpoint


class GenEndpointFailure(BaseModel):
    error: str


class GenEndpointResult(BaseModel):
    result: Union[
        GenEndpointSuccess,
        GenEndpointFailure,
    ]


PROMPT_USAGE_SYSTEM = '''
You are an expert C/C++ developer.

Your task is to implement the body of a function that invokes some API in a target library.

The function must respect all API usage constraints in the target library (both explicit and implicit).

The function will be used in a fuzzing environment, so use the following macros to generate random values for any fuzzable arguments:
- `FUZZ_PARAM(T)` evaluates to a random value of type `T` (where `T` is any fixed-size non-pointer type)
- `FUZZ_PARAM_STR(N)` evaluates to a `const char *` pointing to a random N-byte string
- `FUZZ_PARAM_FILE(N)` evaluates to a `const char *` pointing to a temporary file filled with N bytes of fuzzable data
- `FUZZ_BAIL()` marks an error case that should not continue

Use `FUZZ_PARAM_FILE` instead of `FUZZ_PARAM_STR` for any argument that should be a file path.

Important notes:
- Do not `return` or `throw`; use `FUZZ_BAIL()` to mark an error case that should not continue.
- Use `gf_malloc`/`gf_free` to manage memory (if not using the library API).
- Do not leave any TODOs or placeholders in the code.
- Ensure that you use all provided arguments in the body of the function.
- Do not reference struct fields which are hidden by the library API.
- Do not reimplement checks that are already performed by the library API.
- Do not call any private library functions.
- Do not perform any unsafe operations or illegal type casts.

You have access to the following tools that can help you implement the function:
- `search_ctags`: search the codebase for the given identifier (e.g. function name, type name, struct name, etc.)
- `list_directory`: list the files in the given directory
- `read_file`: read the contents of the given file

Once you understand how to implement the function, use the `EndpointFunctionCompletion` tool to return the function body in a structured format.
- only return the function body in "function_body", do not include the function signature or any other text
- do not emit any #include or #define statements
'''.strip()

PROMPT_USAGE_USER = '''
Implement the following function body:
```cpp
void invoke_{function}({sig}) {{
    // Your implementation here
    // You must:
    // 1. Validate input parameters
    // 2. Invoke "{function}" with appropriate arguments
    // 3. Initialize any output arguments
    // 4. Clean up any temporary objects you create
    // 5. Call FUZZ_BAIL() if any operation fails

    // NOTES:
    // - Do NOT reimplement any parts of the function "{function}", just invoke it with appropriate arguments
    // - If "{function}" is simple, and takes the same arguments as the function you are implementing, you can just invoke it directly
}}
```
'''.strip()

PROMPT_LIFETIME_CONSTRUCT = '''
Implement the following function that constructs an object.

If it is possible, use the library API directly to construct the object.
```cpp
void construct_object({sig}) {{
    // Your implementation here
    // You must:
    // 1. Initialize the object using the library API (if possible)
    // 2. Use `gf_malloc`/`gf_free` to manage memory (if not using the library API)
    // 3. Call FUZZ_BAIL() if any operation fails
    // 4. Clean up any temporary objects you create
}}
```
'''.strip()

PROMPT_LIFETIME_DESTRUCT = '''
Implement the following function that destructs an object:
```cpp
void destruct_object({sig}) {{
    // Your implementation here
    // You must:
    // 1. Clean up the object using the library API (if possible)
    // 2. Use `gf_free` to free memory (if not using the library API)
    // 3. Call FUZZ_BAIL() if any operation fails
}}
```
'''.strip()


def initialize(state: GenEndpointState):
    print('[*] Initializing state')
    sig = state.stub.mock_function_sig()
    str_function_args = ', '.join(map(lambda x: f'"{x}"', state.stub.mock_function_args()))

    messages = []
    messages.append(SystemMessage(content=PROMPT_USAGE_SYSTEM))

    match state.type:
        case 'function':
            messages.append(SystemMessage(content=PROMPT_USAGE_USER.format(
                function=state.stub.function_name,
                sig=sig,
                function_args=str_function_args,
            )))
        case 'construct':
            messages.append(SystemMessage(content=PROMPT_LIFETIME_CONSTRUCT.format(
                sig=sig,
                function_args=str_function_args,
            )))
        case 'destruct':
            messages.append(SystemMessage(content=PROMPT_LIFETIME_DESTRUCT.format(
                sig=sig,
                function_args=str_function_args,
            )))

    # Initialize the state
    return {
        'messages': messages,
        'remaining_attempts': 8,
        'res': None,
    }


def act(state: GenEndpointState):
    print('[*] Generating template')

    # Invoke LLM
    llm = ChatOpenAI(model=state.model).bind_tools([
        tools.search_ctags,
        tools.list_directory,
        tools.read_file,
        EndpointFunctionCompletion
    ], tool_choice='required', parallel_tool_calls=False, strict=True)
    res = llm.invoke(state.messages)

    return {
        "messages": [res],
        "remaining_attempts": state.remaining_attempts - 1,
    }


def check(state: GenEndpointState) -> dict:
    print('[*] Checking template')

    ai_message = state.messages[-1]
    tool_call = ai_message.tool_calls[0]

    completion = EndpointFunctionCompletion(**tool_call['args'])

    # Validate the completion
    err = completion.validate(state.stub)
    if err is not None:
        print(f'(err) {err}')
        return {
            "messages": [ToolMessage(content=err, tool_call_id=tool_call['id'])],
            "res": GenEndpointResult(result=GenEndpointFailure(error=err)),
        }

    # Build the endpoint
    endpoint = state.stub.to_endpoint(completion.function_body)
    res = try_build_external(endpoint, state.info)
    if res is None:
        print('(ok) build')
        return {
            "res": GenEndpointResult(result=GenEndpointSuccess(endpoint=endpoint)),
        }
    else:
        print('(err) build')
        return {
            "messages": [ToolMessage(content=f'Encountered an error while building the endpoint:\n{res.error}', tool_call_id=tool_call['id'])],
            "res": GenEndpointResult(result=GenEndpointFailure(error=res.error)),
        }


def check_state(state: GenEndpointState) -> Literal['bail', 'check', 'tools']:
    if state.remaining_attempts <= 0:
        return 'bail'
    ai_message = state.messages[-1]
    tool_name = ai_message.tool_calls[0]['name']
    if tool_name == 'EndpointFunctionCompletion':
        return 'check'
    else:
        return 'tools'


def check_success(state: GenEndpointState) -> Literal['success', 'failure', 'bail']:
    if state.remaining_attempts <= 0:
        return 'bail'
    if isinstance(state.res.result, GenEndpointSuccess):
        return 'success'
    else:
        return 'failure'


def build_gen_endpoint():
    workflow = StateGraph(GenEndpointState)
    workflow.add_node('initialize', initialize)
    workflow.add_node('act', act)
    workflow.add_node('tools', ToolNode([
        tools.search_ctags,
        tools.list_directory,
        tools.read_file,
    ]))
    workflow.add_node('check', check)

    workflow.add_edge('initialize', 'act')
    workflow.add_conditional_edges('act', check_state, {
        'tools': 'tools',
        'check': 'check',
        'bail': END,
    })
    workflow.add_edge('tools', 'act')
    workflow.add_conditional_edges('check', check_success, {
        'success': END,
        'failure': 'act',
        'bail': END,
    })

    workflow.set_entry_point('initialize')
    return workflow.compile()


def run_gen_endpoint(info: ProjectInfo, model: str, stub: EndpointStub, type: Literal['function', 'construct', 'destruct']) -> GenEndpointResult:
    # Implement basic destruct stub
    if type == 'destruct':
        endpoint = stub.to_endpoint('// empty')
        return GenEndpointResult(result=GenEndpointSuccess(endpoint=endpoint))
    
    state = GenEndpointState(
        info=info,
        model=model,
        stub=stub,
        type=type,
    )
    graph = build_gen_endpoint()
    res = graph.invoke(state)
    return res['res']


def main(args):
    info = ProjectInfo.model_validate_json(open(args.info).read())
    stub = EndpointStub.model_validate_json(open(args.stub).read())
    
    set_global_project_info(info)
    res = run_gen_endpoint(info, args.model, stub, args.type)
    
    print(res.model_dump_json(indent=2))


def register(subparsers):
    parser = subparsers.add_parser('gen-endpoint')
    parser.add_argument('--info', type=str, required=True)
    parser.add_argument('--model', type=str, required=True)
    parser.add_argument('--stub', type=str, required=True)
    parser.add_argument('--type', type=str, required=True, choices=['function', 'construct', 'destruct'])
    parser.set_defaults(func=main)
