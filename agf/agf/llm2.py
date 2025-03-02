from typing import List, Tuple, Optional
from pathlib import Path
import json
import random
from openai import OpenAI
from pydantic import BaseModel
import re

from .spec import Objects, Stubs, Functions, Stub, Template, TemplateError, Endpoint, Schema, ObjectArg, DetailedStub, DetailedTemplate
from .tools.search import find_function_invocations
from .builder import try_build

class TraceMeta(BaseModel):
    type: str
    name: str
    is_retry: bool

class Tracer:
    def __init__(self, logfile: str):
        self.logfile = logfile
        self.traces = []

    def trace(self, messages: List[any], meta: TraceMeta):
        self.traces.append({
            "messages": messages,
            "meta": meta.model_dump()
        })
        self.save()

    def save(self):
        with open(self.logfile, 'w') as f:
            f.write(json.dumps(self.traces, indent=2))

class LLM:
    def __init__(self, model: str, max_retries: int = 3, logfile: Optional[str] = None):
        self.client = OpenAI()
        self.model = model
        self.max_retries = max_retries
        self.tracer = None
        if logfile is not None:
            self.tracer = Tracer(logfile)

    def query(self, messages: List[any], fmt, meta=None):
        completion = self.client.beta.chat.completions.parse(
            model=self.model,
            messages=messages,
            response_format=fmt
        )
        res = completion.choices[0].message.parsed

        if self.tracer is not None:
            self.tracer.trace(
                messages + [{"role": "assistant", "content": res.model_dump_json(indent=2)}],
                meta
            )

        return res

    def prompt_objects(self, code: str) -> Objects:
        '''Generates a list of objects from the given code.'''
        
        task = '''
Given the following code, generate a list of all live-objects that exist beyond a single function call. These objects must have explicit ways to construct and destruct them.

For each object, assign a name and a canonical type.

Include each object only once, do not include multiple variations of the same object.

Prefer the type `struct foo *` over `struct foo`.
'''.strip()

        s = self.query([
            {"role": "system", "content": task},
            {"role": "user", "content": '// code:\n' + code}
        ], Objects, meta=TraceMeta(type='objects', name='objects', is_retry=False))

        return s
    
    def prompt_functions(self, code: str, objects: Objects) -> Stubs:
        '''Generates a list of functions from the given code.'''
        
        task = '''
Given the following code, generate a list of all public API functions in the code.
'''.strip()
        
        s = self.query([
            {"role": "system", "content": task},
            {"role": "user", "content": '// code:\n' + code}
        ], Functions, meta=TraceMeta(type='functions', name='functions', is_retry=False))

        return s
    
    def prompt_stubs(self, code: str, example_usage: str, function: str, objects: Objects) -> Stubs:
        '''Generates a list of stubs from the given code.'''

        task = f'''
Given the following code, list of available objects, and example usage of the function `{function}`, describe the function signature for `{function}`.

Iterate over each argument (and the return value) and describe each argument's:
- "name"
- "type" C/C++ type
- "base_type" the underlying object matching this type (must match an entry in the provided list of objects or "none" if none of the object types match)
- "dir" which can be either:
    - "new" if the argument is being created (i.e. an output of the function)
    - "del" if the argument is being destructed (use the name of the function and docs to understand if this is the case) -- e.g. functions with words like "free" or "delete"
    - "*" in all other cases
- "is_optional" true if the argument can be null, false otherwise
'''.strip()

        res = self.query([
            {"role": "system", "content": task},
            {"role": "user", "content": '// code:\n' + code},
            {"role": "user", "content": '// objects:\n' + objects.model_dump_json(indent=2)},
            {"role": "user", "content": '// example usage:\n' + example_usage}
        ], DetailedStub, meta=TraceMeta(type='stubs', name=function, is_retry=False))

        res.fixup(objects)
        stubs = res.expand_stubs()

        return stubs

    def prompt_lifetime(self, code: str, object: str, constructor: bool, history: List[Tuple[Template, TemplateError]] = []) -> Tuple[DetailedTemplate, Template]:
        if constructor:
            task = f'''
Complete the following function that constructs `{object}`.

Use the library API to initialize the object if possible.
Otherwise, you can directly manage memory with `gf_malloc`/`gf_free` (instead of `malloc`/`free`).

You have access to the `gf_rand` api:
```cpp
// Retrieve a random value for a fixed-size type T
T gf_rand::get<T>();

// Retrieve a random std::string of length len
std::string gf_rand::get_string(size_t len);
```

Use `gf_rand` to generate random values for any arguments that are not already initialized.

Use `gf_bail()` to mark an error case that should not continue.

Do not use the `return` statement anywhere. 

Function signature:
```cpp
void construct_object(
    {object} &__obj,
) {{
    // Your implementation here
    // You must:
    // 1. Initialize __obj
    // 2. Clean up any temporary objects you create
    // 3. Call gf_bail() if any operation fails
}}
```

Return an object with the following fields:
- "function_name" the name of the function
- "function_args" a list of arguments
- "function_body" the body of the function, not including the function signature

"function_args" must be: [
    "{object} &__obj"
]
            '''.strip()
        else:
            task = f'''
Complete the following function that destructs `{object}`.

Use the library API to destruct the object if possible.
Otherwise, you can directly manage memory with `gf_malloc`/`gf_free` (instead of `malloc`/`free`).

You have access to the `gf_rand` api:
```cpp
// Retrieve a random value for a fixed-size type T
T gf_rand::get<T>();

// Retrieve a random std::string of length len
std::string gf_rand::get_string(size_t len);
```

Use `gf_rand` to generate random values for any arguments that are not already initialized.

Use `gf_bail()` to mark an error case that should not continue.

Do not use the `return` statement anywhere. 

Function signature:
```cpp
void destruct_object(
    {object} &__obj, /* delete */
) {{
    // Your implementation here
    // You must:
    // 1. Destruct __obj
    // 2. Clean up any temporary objects you create
    // 3. Call gf_bail() if any operation fails
}}
```

Return an object with the following fields:
- "function_name" the name of the function
- "function_args" a list of arguments
- "function_body" the body of the function, not including the function signature

"function_args" must be: [
    "{object} &__obj"
]
            '''.strip()

        messages = [    
            {"role": "system", "content": task},
            {"role": "user", "content": '// code:\n' + code}
        ]

        for template, error in history:
            messages.append({"role": "assistant", "content": template.model_dump_json(indent=2)})
            messages.append({"role": "user", "content": f'// Fix the error:\n{error.error}'})

        orig = self.query(messages, DetailedTemplate, meta=TraceMeta(type='lifetime', name=f'{"construct" if constructor else "destruct"} {object}', is_retry=len(history) > 0))
        extracted = Template(template=orig.function_body)

        return orig, extracted
    
    def prompt_usage(self, code: str, example_usage: str, function: str, stub: Stub, objects: Objects, history: List[Tuple[DetailedTemplate, TemplateError]] = []) -> Tuple[DetailedTemplate, Template]:
        '''Generates a usage example for the given function stub.'''

        sig_lines = []
        function_args = []

        inputs = [o for o in stub.object_args if o.dir != 'new']
        outputs = [o for o in stub.object_args if o.dir == 'new']

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

        function_args = ', '.join(map(lambda x: f'"{x}"', function_args))

        task = f'''
Complete the following function that invokes `{function}`. The function takes pre-initialized input parameters and must initialize all output parameters inside the body.

You have access to the `gf_rand` api:
```cpp
// Retrieve a random value for a fixed-size type T
T gf_rand::get<T>();

// Retrieve a random std::string of length len
std::string gf_rand::get_string(size_t len);
```

Use `gf_rand` to generate random values for any arguments that are not already initialized.

Use `gf_bail()` to mark an error case that should not continue.

Do not use the `return` statement anywhere. 

Function signature:
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
        
        messages = [
            {"role": "system", "content": task},
            {"role": "user", "content": '// code:\n' + code},
            {"role": "user", "content": '// example usage:\n' + example_usage}
        ]

        for template, error in history:
            messages.append({"role": "assistant", "content": template.model_dump_json(indent=2)})
            messages.append({"role": "user", "content": f'// Fix the error:\n{error.error}'})

        orig = self.query(messages, DetailedTemplate, meta=TraceMeta(type='usage', name=function, is_retry=len(history) > 0))
        extracted = Template(template=orig.function_body)

        return orig, extracted


def register(subparsers):
    parser = subparsers.add_parser('objects', help='Generate a list of objects from the given code')
    parser.add_argument('code', help='Path to code file')
    parser.add_argument('-o', '--output', help='Path to output file')
    parser.set_defaults(func=objects)

    parser = subparsers.add_parser('functions', help='Generate a list of functions from the given code')
    parser.add_argument('code', help='Path to code file')
    parser.add_argument('-o', '--output', help='Path to output file')
    parser.set_defaults(func=functions)

    parser = subparsers.add_parser('stubs', help='Generate a list of stubs from the given code')
    parser.add_argument('code', help='Path to code file')
    parser.add_argument('function', help='Name of function to generate stubs for')
    parser.add_argument('objects', help='Path to objects file')
    parser.add_argument('codebase', help='Path to codebase directory')
    parser.add_argument('-o', '--output', help='Path to output file')
    parser.set_defaults(func=stubs)

    parser = subparsers.add_parser('lifetime', help='Generate a template for the given object')
    parser.add_argument('code', help='Path to code file')
    parser.add_argument('object', help='Name of object to generate template for')
    parser.add_argument('objects', help='Path to objects file')
    parser.add_argument('-c', '--constructor', action='store_true', help='Generate a constructor template')
    parser.add_argument('-d', '--destructor', action='store_true', help='Generate a destructor template')
    parser.add_argument('-o', '--output', help='Path to output file')
    parser.set_defaults(func=lifetime)

    parser = subparsers.add_parser('lifetime-loop', help='Generate and test lifetime templates with retries')
    parser.add_argument('code', help='Path to code file')
    parser.add_argument('object', help='Name of object to generate template for')
    parser.add_argument('objects', help='Path to objects file')
    parser.add_argument('build_script', help='Path to build script')
    parser.add_argument('-c', '--constructor', action='store_true', help='Generate a constructor template')
    parser.add_argument('-d', '--destructor', action='store_true', help='Generate a destructor template')
    parser.add_argument('-o', '--output', help='Path to output file')
    parser.add_argument('--config', help='Path to config file')
    parser.set_defaults(func=lifetime_loop)

    parser = subparsers.add_parser('usage', help='Generate a usage example for the given function stub')
    parser.add_argument('code', help='Path to code file')
    parser.add_argument('function', help='Name of function to generate usage for')
    parser.add_argument('stubs', help='Path to stubs file')
    parser.add_argument('objects', help='Path to objects file')
    parser.add_argument('codebase', help='Path to codebase directory')
    parser.add_argument('-o', '--output', help='Path to output file')
    parser.set_defaults(func=usage)

    parser = subparsers.add_parser('usage-loop', help='Generate a usage example for the given function stub')
    parser.add_argument('code', help='Path to code file')
    parser.add_argument('function', help='Name of function to generate usage for')
    parser.add_argument('stubs', help='Path to stubs file')
    parser.add_argument('objects', help='Path to objects file')
    parser.add_argument('codebase', help='Path to codebase directory')
    parser.add_argument('build_script', help='Path to build script')
    parser.add_argument('-o', '--output', help='Path to output file')
    parser.add_argument('-c', '--config', help='Path to config file')
    parser.set_defaults(func=usage_loop)

    parser = subparsers.add_parser('full-loop', help='Generate a usage example for the given function stub')
    parser.add_argument('code', help='Path to code file')
    parser.add_argument('codebase', help='Path to codebase directory')
    parser.add_argument('build_script', help='Path to build script')
    parser.add_argument('-o', '--output', help='Path to output file')
    parser.add_argument('-c', '--config', help='Path to config file')
    parser.add_argument('-l', '--logfile', help='Path to logfile')
    parser.set_defaults(func=full_loop)

def get_example_usage(codebase: Path, function: str, context_lines: int = 7) -> str:
    codebase = Path(codebase)
    invocations = find_function_invocations(codebase, function, context_lines=context_lines)

    sample = min(6, len(invocations))
    if sample:
        invocations = random.sample(invocations, sample)

    # Print results with nice formatting
    output = ''
    for filename, line_num, context in invocations:
        output += f"\n{filename}:{line_num}\n"
        output += ("-" * 60) + '\n'
        
        for i, ctx_line in enumerate(context):
            # Calculate the actual line number for this context line
            ctx_line_num = line_num - (len(context) // 2) + i
            prefix = f"{ctx_line_num:4d} | "
            
            # Highlight the matching line
            if ctx_line_num == line_num:
                output += f"{prefix}>>> {ctx_line.rstrip()}\n"
            else:
                output += f"{prefix}    {ctx_line.rstrip()}\n"
        output += ("-" * 60) + '\n'

    return output


def objects(args):
    llm = LLM('gpt-4o-mini')
    code = open(args.code).read()
    objects = llm.prompt_objects(code)
    j = objects.model_dump_json(indent=2)
    if args.output:
        with open(args.output, 'w') as f:
            f.write(j)
    else:
        print(j)


def functions(args):
    llm = LLM('gpt-4o-mini')
    code = open(args.code).read()
    objects = llm.prompt_objects(code)
    functions = llm.prompt_functions(code, objects)
    j = functions.model_dump_json(indent=2)
    if args.output:
        with open(args.output, 'w') as f:
            f.write(j)
    else:
        print(j)


def stubs(args):
    llm = LLM('gpt-4o-mini')
    code = open(args.code).read()
    objects = Objects.model_validate_json(open(args.objects).read())
    example_usage = get_example_usage(args.codebase, args.function)

    stubs = llm.prompt_stubs(code, example_usage, args.function, objects)
    j = stubs.model_dump_json(indent=2)
    if args.output:
        with open(args.output, 'w') as f:
            f.write(j)
    else:
        print(j)


def lifetime(args):
    llm = LLM('gpt-4o-mini')
    code = open(args.code).read()
    objects = Objects.model_validate_json(open(args.objects).read())
    object = next(o for o in objects.objects if o.name == args.object)
    constructor = args.constructor
    orig, extracted = llm.prompt_lifetime(code, object, constructor)
    print(extracted.template)


def usage(args):
    llm = LLM('gpt-4o-mini')
    code = open(args.code).read()
    stubs = Stubs.model_validate_json(open(args.stubs).read())
    stub = stubs.stubs[0]
    objects = Objects.model_validate_json(open(args.objects).read())
    example_usage = get_example_usage(args.codebase, args.function)

    orig, extracted = llm.prompt_usage(code, example_usage, args.function, stub, objects)
    print(extracted.template)

    endpoint = stub.to_endpoint(args.function, extracted.template)
    print(endpoint)

    # Write the endpoint to a file
    with open(args.output, 'w') as f:
        f.write(endpoint.model_dump_json(indent=2))


def usage_loop(args):
    llm = LLM('gpt-4o-mini')
    code = open(args.code).read()
    stubs = Stubs.model_validate_json(open(args.stubs).read())
    stub = stubs.stubs[0]
    objects = Objects.model_validate_json(open(args.objects).read())
    example_usage = get_example_usage(args.codebase, args.function)

    history = []
    for i in range(5):
        print(f'Attempt {i + 1}')

        orig, extracted = llm.prompt_usage(code, example_usage, args.function, stub, objects, history=history)
        print(extracted.template)

        # Sanity check the format
        if re.match(r'^\s*void\s+\w+\s*\(', extracted.template):
            err = 'Do not include the function signature in the function body'
            history.append((orig, TemplateError(error=err)))
            continue

        endpoint = stub.to_endpoint(args.function, extracted.template)
        res = try_build(endpoint, args.build_script, config=args.config)
        
        if res is None:
            print('Success!')
            break
        
        else:
            print('Build failed, retrying...')
            print('-' * 60)
            print(res.error)
            print('-' * 60)
            history.append((orig, TemplateError(error=res.error)))

def _usage_loop(llm, code, function, stub, objects, example_usage, build_script, config, max_attempts: int = 5) -> Optional[Endpoint]:
    history = []
    for i in range(max_attempts):
        print(f'Attempt {i + 1}...', end=' ')

        orig, extracted = llm.prompt_usage(code, example_usage, function, stub, objects, history=history)
        print(extracted.template)

        # Sanity check the format
        if re.match(r'^\s*void\s+\w+\s*\(', extracted.template):
            err = 'Do not include the function signature in the function body'
            history.append((orig, TemplateError(error=err)))
            continue

        endpoint = stub.to_endpoint(function, extracted.template)
        res = try_build(endpoint, build_script, config=config)

        if res is None:
            print('Success!')
            return endpoint
        else:
            print('Fail')
            print('-' * 60)
            print(res.error)
            print('-' * 60)
            history.append((orig, TemplateError(error=res.error)))

    return None

def _lifetime_loop(llm, code, object, objects, constructor, build_script, config, max_attempts: int = 5) -> Optional[Endpoint]:
    
    stub = Stub(object_args=[ObjectArg(name='obj', typ=object, dir='new' if constructor else 'del')])
    stub_name = f'{"construct" if constructor else "destruct"}_{object}'

    history = []
    for i in range(max_attempts):
        print(f'Attempt {i + 1}...', end=' ')

        orig, extracted = llm.prompt_lifetime(code, object, constructor, history=history)
        print(extracted.template)

        # Sanity check the format
        if re.match(r'^\s*void\s+\w+\s*\(', extracted.template):
            err = 'Do not include the function signature in the function body'
            history.append((orig, TemplateError(error=err)))
            continue

        endpoint = stub.to_endpoint(stub_name, extracted.template)
        res = try_build(endpoint, build_script, config=config)
        
        if res is None:
            print('Success!')
            return endpoint
        else:
            print('Fail')
            print('-' * 60)
            print(res.error)
            print('-' * 60)
            history.append((orig, TemplateError(error=res.error)))

    return None

def lifetime_loop(args):
    llm = LLM('gpt-4o-mini')
    code = open(args.code).read()
    objects = Objects.model_validate_json(open(args.objects).read())
    object = next(o for o in objects.objects if o.name == args.object)

    if not args.constructor and not args.destructor:
        print('Must specify either --constructor or --destructor')
        return

    endpoint = _lifetime_loop(llm, code, object, objects, args.constructor, args.build_script, args.config)
    if endpoint is not None:
        if args.output:
            with open(args.output, 'w') as f:
                f.write(endpoint.model_dump_json(indent=2))
        else:
            print(endpoint.model_dump_json(indent=2))

def full_loop(args):
    code = open(args.code).read()
    codebase = Path(args.codebase)
    build_script = args.build_script
    config = args.config

    llm = LLM('gpt-4o-mini', logfile=args.logfile)
    objects = llm.prompt_objects(code)
    functions = llm.prompt_functions(code, objects)
    
    print(f'{len(objects.objects)} objects')
    for obj in objects.objects:
        print(f'- "{obj}"')
    print(f'{len(functions.functions)} functions')

    fstubs = []

    for function in functions.functions:
        example_usage = get_example_usage(codebase, function)
        stubs = llm.prompt_stubs(code, example_usage, function, objects)
        print(f' - {function} ({len(stubs.stubs)} stubs)')
        for stub in stubs.stubs:
            # print [(dir) type name, ...]
            print(f'[{", ".join([f"({o.dir}) {o.typ} {o.name}" for o in stub.object_args])}]')
            fstubs.append((function, stub))


    # Iterate and check if we have constructors/destructors for each object
    auto_lifetimes = set() # (object, constructor)
    for _func, stub in fstubs:
        for arg in stub.object_args:
            if arg.dir == 'new':
                auto_lifetimes.add((arg.typ, True))
            elif arg.dir == 'del':
                auto_lifetimes.add((arg.typ, False))

    # Generate usage examples for each function stub
    endpoints = []

    for object in objects.objects:
        for constructor in [True, False]:
            if (object, constructor) in auto_lifetimes:
                print(f'Skipping {"constructor" if constructor else "destructor"} for {object} (auto-generated)')
                continue

            print(f'Generating {"constructor" if constructor else "destructor"} for {object}...')
            endpoint = _lifetime_loop(llm, code, object, objects, constructor, build_script, config)
            if endpoint is not None:
                endpoints.append(endpoint)

    for function, stub in fstubs:
        print(f'Generating usage for {function}...')
        example_usage = get_example_usage(codebase, function)
        endpoint = _usage_loop(llm, code, function, stub, objects, example_usage, build_script, config)
        if endpoint is not None:
            endpoints.append(endpoint)

    print(f'Generated {len(endpoints)} endpoints')
    
    schema = Schema(endpoints=endpoints)
    j = schema.model_dump_json(indent=2)

    schema.pprint()

    with open(args.output, 'w') as f:
        f.write(j)
