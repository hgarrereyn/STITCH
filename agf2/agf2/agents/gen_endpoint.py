
from typing import Union, Annotated, Optional, Literal, List
import operator
from pydantic import BaseModel
from langgraph.graph import StateGraph, START, END
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import ToolNode
from langchain_core.runnables.config import RunnableConfig
from ..utils.spec import Endpoint, EndpointStub, EndpointFunctionCompletion
from ..utils.builder import try_build_external
from ..utils import tools
from ..utils.info import ProjectInfo


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

Your task is to implement the body of a function that exercises some API in a target library.

The function must respect all API usage constraints in the target library (both explicit and implicit).

Your function will be used in a fuzzing environment, use the following macros to generate random values for any fuzzable arguments.
These macros evaluate to the same value for the duration of the function call, so don't call them in a loop if you need different values.
```cpp
// Sets x to a random fixed-size value of type T
T x = FUZZ_PARAM(T);

// Sets x to a random const char * of length N (N must be an integer literal)
const char *x = FUZZ_PARAM_STR(N);

// Sets filename to a temporary file filled with N bytes of fuzzable data (N must be an integer literal)
const char *filename = FUZZ_PARAM_FILE(N);

// Marks an error case that should not continue
FUZZ_BAIL();
```

Important notes:
- Do not throw exceptions or emit `return` statements. Instead, use `FUZZ_BAIL()` to mark an error case that should not continue (i.e. if the function returns an error code).
- Use `gf_malloc`/`gf_free` to manage memory (if not using the library API).
- Do not leave any TODOs or placeholders in the code.
- Ensure that you use all provided arguments in the body of the function.
- Avoid referencing struct fields which are hidden by the library API.
- If the function requires a filename, use `FUZZ_PARAM_FILE(N)` to get a random filename.

You have access to the following tools that can help you query the codebase of the target library:
- search_codebase: search the codebase for the given query
- list_directory: list the files in the given directory
- read_file: read the contents of the given file
'''.strip()

PROMPT_USAGE_USER = '''
Implement the following function body:
```cpp
void invoke_{function}({sig}) {{
    // Your implementation here
    // You must:
    // 1. Validate input parameters
    // 2. Call {function} with appropriate arguments
    // 3. Initialize any output arguments
    // 4. Clean up any temporary objects you create
    // 5. Call FUZZ_BAIL() if any operation fails
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

PROMPT_COMPLETE_USER = '''
Now that you have implemented the function, please return encode it in a structured format.

Important notes:
- only return the function body in "function_body", do not include the function signature or any other text
- do not emit any #include or #define statements
'''.strip()


def initialize_state(state: GenEndpointState):
    print('[*] Initializing state')
    sig = state.stub.mock_function_sig()
    str_function_args = ', '.join(map(lambda x: f'"{x}"', state.stub.mock_function_args()))

    messages = []
    messages.append({
        'role': 'system',
        'content': PROMPT_USAGE_SYSTEM,
    })

    match state.type:
        case 'function':
            messages.append({
                'role': 'user',
                'content': PROMPT_USAGE_USER.format(
                    function=state.stub.function_name,
                    sig=sig,
                    function_args=str_function_args,
                ),
            })
        case 'construct':
            messages.append({
                'role': 'user',
                'content': PROMPT_LIFETIME_CONSTRUCT.format(
                    sig=sig,
                    function_args=str_function_args,
                ),
            })
        case 'destruct':
            messages.append({
                'role': 'user',
                'content': PROMPT_LIFETIME_DESTRUCT.format(
                    sig=sig,
                    function_args=str_function_args,
                ),
            })

    # Initialize the state
    return {
        'messages': messages,
        'remaining_attempts': 8,
        'res': None,
    }


def agent(state: GenEndpointState):
    print('[*] Generating template')

    # Invoke LLM
    llm = ChatOpenAI(model=state.model).bind_tools([
        tools.search_codebase,
        tools.list_directory,
        tools.read_file,
    ])
    res = llm.invoke(state.messages)
    return {
        "messages": [res],
        "remaining_attempts": state.remaining_attempts - 1,
    }


def agent_init_complete(state: GenEndpointState):
    print('[*] Initializing completion')
    return {
        "messages": [{"role": "user", "content": PROMPT_COMPLETE_USER}],
    }


def agent_complete(state: GenEndpointState):
    print('[*] Completing function')

    # Invoke LLMl
    llm = ChatOpenAI(model=state.model).with_structured_output(EndpointFunctionCompletion)
    completion: EndpointFunctionCompletion = llm.invoke(state.messages)

    return {
        "messages": [{
            "role": "assistant",
            "content": completion.model_dump_json(),
        }],
        "completion": completion,
        "remaining_attempts": state.remaining_attempts - 1,
    }


def check_completion(state: GenEndpointState) -> dict:
    print('[*] Checking template')

    # Validate the completion
    err = state.completion.validate(state.stub)
    if err is not None:
        print(f'(err) {err}')
        return {
            "messages": [{"role": "user", "content": err}],
            "res": GenEndpointResult(result=GenEndpointFailure(error=err)),
        }

    # Build the endpoint
    endpoint = state.stub.to_endpoint(state.completion.function_body)
    # res = try_build(endpoint, state.info.build_script, config=state.info.config)
    res = try_build_external(endpoint, state.info.build_script_ext, state.info.config)
    if res is None:
        print('(ok) build')
        return {
            "res": GenEndpointResult(result=GenEndpointSuccess(endpoint=endpoint)),
        }
    else:
        print('(err) build')
        return {
            "messages": [{"role": "user", "content": f'Fix the following error:\n{res.error}'}],
            "res": GenEndpointResult(result=GenEndpointFailure(error=res.error)),
        }


def check_state(state: GenEndpointState) -> Literal['done', 'complete', 'tools']:
    if state.remaining_attempts <= 0:
        return 'done'
    ai_message = state.messages[-1]
    if hasattr(ai_message, "tool_calls") and len(ai_message.tool_calls) > 0:
        return 'tools'
    else:
        return 'complete'
    

def check_success(state: GenEndpointState) -> Literal['success', 'failure', 'bail']:
    if state.remaining_attempts <= 0:
        return 'bail'
    if isinstance(state.res.result, GenEndpointSuccess):
        return 'success'
    else:
        return 'failure'


def build_gen_endpoint():
    workflow = StateGraph(GenEndpointState)
    workflow.add_node('initialize_state', initialize_state)
    workflow.add_node('agent', agent)
    workflow.add_node('agent_init_complete', agent_init_complete)
    workflow.add_node('agent_complete', agent_complete)
    workflow.add_node('check_completion', check_completion)
    workflow.add_node('tools', ToolNode([
        tools.search_codebase,
        tools.list_directory,
        tools.read_file,
    ]))

    workflow.add_edge('initialize_state', 'agent')
    workflow.add_conditional_edges('agent', check_state, {
        'tools': 'tools',
        'complete': 'agent_init_complete',
        'done': END,
    })
    workflow.add_edge('tools', 'agent')
    workflow.add_edge('agent_init_complete', 'agent_complete')
    workflow.add_edge('agent_complete', 'check_completion')
    workflow.add_conditional_edges('check_completion', check_success, {
        'success': END,
        'failure': 'agent_complete',
        'bail': END,
    })

    workflow.set_entry_point('initialize_state')
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
    info = ProjectInfo.model_validate_json(args.info)
    stub = EndpointStub.model_validate_json(args.stub)
    res = run_gen_endpoint(info, args.model, stub, args.type)
    
    if isinstance(res.result, GenEndpointSuccess):
        res.result.endpoint.pprint()
    else:
        print(f'(err) {res.result.error}')


def register(subparsers):
    parser = subparsers.add_parser('gen-endpoint')
    parser.add_argument('--info', type=str, required=True)
    parser.add_argument('--model', type=str, required=True)
    parser.add_argument('--stub', type=str, required=True)
    parser.add_argument('--type', type=str, required=True, choices=['function', 'construct', 'destruct'])
    parser.set_defaults(func=main)
