
from typing import Union, Annotated, Optional, Literal, List
import operator
import re
from pydantic import BaseModel
from langgraph.graph import StateGraph, START, END
from langchain_openai import ChatOpenAI

from ..utils.spec import Endpoint, Stub, DetailedTemplate
from ..utils.builder import try_build
from ..utils.search import get_example_usage


class EndpointUsageState(BaseModel):
    build_script: str
    config: str
    codebase: str
    header: str
    stub: Stub
    type: Literal['function', 'construct', 'destruct']

    # generated
    remaining_attempts: int = 5
    expected_args: Optional[List[str]] = None
    template: Optional[DetailedTemplate] = None
    messages: Annotated[list, operator.add] = []
    res: Optional['EndpointUsageResult'] = None


class EndpointUsageSuccess(BaseModel):
    endpoint: Endpoint


class EndpointUsageFailure(BaseModel):
    error: str


class EndpointUsageResult(BaseModel):
    result: Union[
        EndpointUsageSuccess,
        EndpointUsageFailure,
    ]


PROMPT_USAGE_SYSTEM = '''
You are an expert C++ developer. You will be given a function signature and asked to implement the body of the function.

You have access to the `gf_rand` api:
```cpp
// Retrieve a random value for a fixed-size type T
T gf_rand::get<T>();

// Retrieve a random std::string of length len
std::string gf_rand::get_string(size_t len);
```

Notes:
- Use `gf_rand` to generate random values for any fuzzable arguments (more randomness is better), but ensure values are within the expected range.
- Use `gf_bail()` to mark an error case that should not continue.
- Do not use the `return` statement anywhere.

Do not leave any TODOs or placeholders in the code.
'''.strip()

PROMPT_USAGE_USER = '''
Implement the body of the following function:
```cpp
void invoke_{function}({sig}) {{
    // Your implementation here
    // You must:
    // 1. Validate input parameters
    // 2. Call {function} with appropriate arguments
    // 3. Initialize any output arguments
    // 4. Clean up any temporary objects you create
    // 5. Call gf_bail() if any operation fails
}}
```

Return an object with the following fields:
- "function_name" the name of the function
- "function_args" a list of arguments
- "function_body" the implementation of the function, not including the function signature

"function_args" must be: [
    {function_args}
]
'''.strip()

PROMPT_LIFETIME_CONSTRUCT = '''
Implement the following function that constructs an object:
```cpp
void construct_object({sig}) {{
    // Your implementation here
    // You must:
    // 1. Initialize the object using the library API (if possible)
    // 2. Use `gf_malloc`/`gf_free` to manage memory (if not using the library API)
    // 3. Call gf_bail() if any operation fails
    // 4. Clean up any temporary objects you create
}}
```

Return an object with the following fields:
- "function_name" the name of the function
- "function_args" a list of arguments
- "function_body" the implementation of the function, not including the function signature

"function_args" must be: [
    {function_args}
]
'''.strip()

PROMPT_LIFETIME_DESTRUCT = '''
Implement the following function that destructs an object:
```cpp
void destruct_object({sig}) {{
    // Your implementation here
    // You must:
    // 1. Clean up the object using the library API (if possible)
    // 2. Use `gf_free` to free memory (if not using the library API)
    // 3. Call gf_bail() if any operation fails
}}
```

Return an object with the following fields:
- "function_name" the name of the function
- "function_args" a list of arguments
- "function_body" the implementation of the function, not including the function signature

"function_args" must be: [
    {function_args}
]
'''.strip()


def initialize_state(state: EndpointUsageState):
    print('[*] Initializing state')
    sig_lines = []
    function_args = []

    inputs = [o for o in state.stub.object_args if o.dir != 'new']
    outputs = [o for o in state.stub.object_args if o.dir == 'new']

    if len(inputs) > 0:
        sig_lines.append('    // Input parameters - these are already initialized, you must use them')
        sig_lines += [f"    {o.typ} __{o.name}," + (" /* delete */" if o.dir == 'del' else "") for o in inputs]
        function_args += [f"{o.typ} __{o.name}" for o in inputs]

    if len(outputs) > 0:
        sig_lines.append('    // Output parameters - you must initialize these')
        sig_lines += [f"    {o.typ}& __{o.name}," for o in outputs]
        function_args += [f"{o.typ}& __{o.name}" for o in outputs]
    sig = ''
    if len(sig_lines) > 0:
        sig = '\n' + '\n'.join(sig_lines) + '\n'

    str_function_args = ', '.join(map(lambda x: f'"{x}"', function_args))

    code = open(state.header, 'rb').read().decode('latin-1')
    
    messages = []
    messages.append({
        'role': 'system',
        'content': PROMPT_USAGE_SYSTEM,
    })

    # messages.append({
    #     'role': 'user',
    #     'content': f'// Process the following code:\n{code}'
    # })
    messages.append({
        'role': 'assistant',
        'content': 'ACK'
    })

    match state.type:
        case 'function':
            example_usage = get_example_usage(state.codebase, state.stub.function)
            messages.append({
                'role': 'user',
                'content': f'// Process the following example usage:\n{example_usage}'
            })
            messages.append({
                'role': 'assistant',
                'content': 'ACK'
            })

            messages.append({
                'role': 'user',
                'content': PROMPT_USAGE_USER.format(
                    function=state.stub.function,
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
        'expected_args': function_args,
        'remaining_attempts': 5,
    }


def generate_template(state: EndpointUsageState):
    print('[*] Generating template')

    # Invoke LLM
    llm = ChatOpenAI(model="gpt-4o-mini").with_structured_output(DetailedTemplate)
    res = llm.invoke(state.messages)
    return {"messages": [{
        "role": "assistant",
        "content": res.model_dump_json(),
    }], "template": res}


def check_template(state: EndpointUsageState):
    print('[*] Checking template')

    if state.remaining_attempts <= 0:
        print('(err) max attempts reached')
        err = 'Max attempts reached'
        return {
            "messages": [{"role": "user", "content": err}],
            "res": EndpointUsageResult(result=EndpointUsageFailure(error=err)),
        }

    # Sanity check the format
    if re.match(r'^\s*void\s+\w+\s*\(', state.template.function_body):
        print('(err) func signature')
        err = 'Do not include the function signature in the function body'
        return {
            "messages": [{"role": "user", "content": err}],
            "res": EndpointUsageResult(result=EndpointUsageFailure(error=err)),
            "remaining_attempts": state.remaining_attempts - 1,
        }

    # Ensure function_args match the signature
    if state.expected_args != state.template.function_args:
        print('(err) func args')
        err = f'Function args do not match expected args {state.expected_args} != {state.template.function_args}'
        return {
            "messages": [{"role": "user", "content": err}],
            "res": EndpointUsageResult(result=EndpointUsageFailure(error=err)),
            "remaining_attempts": state.remaining_attempts - 1,
        }
    
    # Ensure we didn't get a template with embedded __out_x or __in_x arguments
    if re.search(r'\b__out_\w+\b', state.template.function_body) or re.search(r'\b__in_\w+\b', state.template.function_body):
        print('(err) embedded __out_x or __in_x')
        err = 'Do not use __out_x or __in_x in the function body, use the variable names directly'
        return {
            "messages": [{"role": "user", "content": err}],
            "res": EndpointUsageResult(result=EndpointUsageFailure(error=err)),
            "remaining_attempts": state.remaining_attempts - 1,
        }

    # Build the endpoint
    endpoint = state.stub.to_endpoint(state.stub.function, state.template.function_body)
    res = try_build(endpoint, state.build_script, config=state.config)
    if res is None:
        print('(ok) build')
        return {
            "res": EndpointUsageResult(result=EndpointUsageSuccess(endpoint=endpoint)),
            "remaining_attempts": state.remaining_attempts - 1,
        }
    else:
        print('(err) build')
        return {
            "messages": [{"role": "user", "content": f'Fix the following error:\n{res.error}'}],
            "res": EndpointUsageResult(result=EndpointUsageFailure(error=res.error)),
            "remaining_attempts": state.remaining_attempts - 1,
        }


def cond_check_template(state: EndpointUsageState) -> Literal['done', 'retry']:
    if state.remaining_attempts <= 0:
        return 'done'
    if state.res is None:
        return 'retry'
    return 'done' if isinstance(state.res.result, EndpointUsageSuccess) else 'retry'


def build_endpoint_usage():
    workflow = StateGraph(EndpointUsageState)
    workflow.add_node('initialize_state', initialize_state)
    workflow.add_node('generate_template', generate_template)
    workflow.add_node('check_template', check_template)

    workflow.add_edge('initialize_state', 'generate_template')
    workflow.add_edge('generate_template', 'check_template')
    workflow.add_conditional_edges('check_template', cond_check_template, {
        'done': END,
        'retry': 'generate_template',
    })
    workflow.set_entry_point('initialize_state')
    return workflow.compile()


ENDPOINT_USAGE_GRAPH = build_endpoint_usage()


def run_endpoint_usage(build_script: str, config: str, codebase: str, header: str, stub: Stub, type: Literal['function', 'construct', 'destruct']) -> EndpointUsageResult:
    state = EndpointUsageState(
        build_script=build_script,
        config=config,
        codebase=codebase,
        header=header,
        stub=stub,
        type=type,
    )
    res = ENDPOINT_USAGE_GRAPH.invoke(state)
    return res['res']


def main(args):
    stub = Stub.model_validate_json(args.stub)
    res = run_endpoint_usage(args.build_script, args.config, args.codebase, args.header, stub, args.type)
    
    if isinstance(res.result, EndpointUsageSuccess):
        res.result.endpoint.pprint()
    else:
        print(f'(err) {res.result.error}')


def register(subparsers):
    parser = subparsers.add_parser('endpoint-usage')
    parser.add_argument('--build-script', type=str, required=True)
    parser.add_argument('--config', type=str, required=True)
    parser.add_argument('--codebase', type=str, required=True)
    parser.add_argument('--header', type=str, required=True)
    parser.add_argument('--stub', type=str, required=True)
    parser.add_argument('--type', type=str, required=True, choices=['function', 'construct', 'destruct'])
    parser.set_defaults(func=main)
