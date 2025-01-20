from typing import List, Tuple, Optional
from pathlib import Path
import json
import random
from openai import OpenAI

from .spec import Objects, Stubs, Functions, Object, Templates, Stub, Template, TemplateError, Endpoint, Schema, Variable, ObjectArg
from .tools.search import find_function_invocations
from .builder import try_build

class LLM:
    def __init__(self, model: str, max_retries: int = 3):
        self.client = OpenAI()
        self.model = model
        self.max_retries = max_retries

    def query(self, messages: List[str], fmt):
        completion = self.client.beta.chat.completions.parse(
            model=self.model,
            messages=messages,
            response_format=fmt
        )
        return completion.choices[0].message.parsed
    
    def query_multi(self, messages: List[str], fmt, n: int = 1):
        completion = self.client.beta.chat.completions.parse(
            model=self.model,
            messages=messages,
            response_format=fmt,
            n=n
        )
        return [c.message.parsed for c in completion.choices]

    def prompt_objects(self, code: str) -> Objects:
        '''Generates a list of objects from the given code.'''
        
        task = '''
Given the following code, generate a list of all live-objects that exist beyond a single function call. These objects must have explicit ways to construct and destruct them.

For each object, assign a name and a canonical type.

Prefer the type `struct foo *` over `struct foo`.
'''.strip()

        s = self.query([
            {"role": "system", "content": task},
            {"role": "user", "content": '// code:\n' + code}
        ], Objects)

        return s
    
    def prompt_functions(self, code: str, objects: Objects) -> Stubs:
        '''Generates a list of functions from the given code.'''
        
        task = '''
Given the following code, generate a list of all public API functions in the code.
'''.strip()
        
        s = self.query([
            {"role": "system", "content": task},
            {"role": "user", "content": '// code:\n' + code}
        ], Functions)

        return s
    
    def prompt_stubs(self, code: str, example_usage: str, function: str, objects: Objects, n: int = 5) -> Stubs:
        '''Generates a list of stubs from the given code.'''

        task = f'''
Given the following code, list of available objects, and example usage of the function `{function}`, generate a list of stubs for the function `{function}`.

Each stub consists of a list of object arguments. Each object argument has a name, a type (which must match an object name), and a direction (`*`, `new`, or `del`).

If the function produces a new object, the object direction should be `new`. If the function deletes an object (e.g. a destructor), the object direction should be `del`. All other cases should be `*`.

Only list function arguments that have object types. Do not consider arguments that are primitive types (e.g. `int`, `float`, `bool`, `string`, etc.).

Most functions have a single way to call them, therefore just one stub is needed. However, if the function is variadic (for example, optional arguments), generate a stub for each variant. For example if an argument is optional, generate a stub for when it is not provided and a stub for when it is provided.

Refer to the documentation of the function and the example usage to determine the possible arguments.

Ensure that each argument type matches the name of an object in the list of objects.
'''.strip()

        res = self.query_multi([
            {"role": "system", "content": task},
            {"role": "user", "content": '// code:\n' + code},
            {"role": "user", "content": '// objects:\n' + objects.model_dump_json(indent=2)},
            {"role": "user", "content": '// example usage:\n' + example_usage}
        ], Stubs, n=n)

        stubs = Stubs(stubs=[s for r in res for s in r.stubs])
        stubs.fixup(objects)
        stubs.dedup()

        return stubs

    def prompt_lifetime(self, code: str, object: Object, constructor: bool, history: List[Tuple[Template, TemplateError]] = []) -> Template:
        task = f'''
Complete the following code snippet to {'construct' if constructor else 'destruct'} the object `{object.name}` in a way that maximizes diversity of values.

Ensure that you only emit code that is necessary to {'construct' if constructor else 'destruct'} the object.

Prefer to use the library API to allocate/free memory. If there is no library API, use `gf_malloc` and `gf_free` instead.

You may emit statements like:
```
T x = gf_rand::get<T>()
```
to retrieve random values for some arbitrary fixed-size type T.

The input variables are pre-initialized to some value and you may reference them in your code. Do **not** redefine them.
You are responsible for initializing all of the output variables.

Do not add any other inputs or outputs. Any new objects constructed that are not listed as outputs should be deleted.

Do not leave any logic unimplemented or "TODO". Make sure the code snippet is complete.

If some operation fails cleanly, emit the statement `gf_bail();` to mark an error case that should not continue.
```
'''.strip()
        
        if constructor:
            task += f'''
You must construct this object:
```
{object.canonical_type} obj;
```

Complete the following code snippet:
```
// Code:
{{
    /* complete the code here to construct obj */
}}
```
'''.strip()
        else:
            task += f'''
You must destruct this object:
```
{object.canonical_type} obj = /* hidden */;
```

Complete the following code snippet:
```
// Code:
{{
    /* complete the code here to destruct obj */
}}
```
'''.strip()

        messages = [    
            {"role": "system", "content": task},
            {"role": "user", "content": '// code:\n' + code}
        ]

        for template, error in history:
            messages.append({"role": "assistant", "content": template.model_dump_json(indent=2)})
            messages.append({"role": "user", "content": f'// Fix the error:\n{error}'})

        s = self.query(messages, Template)

        return s
    
    def prompt_usage(self, code: str, example_usage: str, function: str, stub: Stub, objects: Objects, history: List[Tuple[Template, TemplateError]] = []) -> Template:
        '''Generates a usage example for the given function stub.'''

        inputs = '\n'.join([f'{objects.find(o.typ).canonical_type} {o.name} = /* hidden */' for i, o in enumerate(stub.object_args) if o.dir != 'new'])
        outputs = '\n'.join([f'{objects.find(o.typ).canonical_type} {o.name};' for i, o in enumerate(stub.object_args) if o.dir == 'new'])

        task = f'''
Complete the following code snippet in a way that maximizes diversity of values and invokes the function `{function}`.
Don't perform any extraneous operations that are not necessary to invoke the function.

You may emit statements like:
```
T x = gf_rand::get<T>()
```
to retrieve random values for some arbitrary fixed-size type T.

The input variables are pre-initialized to some value and you may reference them in your code. Do **not** redefine them.
You are responsible for initializing all of the output variables.

Do not add any other inputs or outputs. Any new objects constructed that are not listed as outputs should be deleted.

Do not leave any logic unimplemented or "TODO". Make sure the code snippet is complete.

If some operation fails cleanly, emit the statement `gf_bail();` to mark an error case that should not continue.

You have access to the following objects:
```cpp
// Inputs:
{inputs}

// Outputs:
{outputs}
```

Complete the following code snippet:
```cpp
// Code:
{{
    // ...
}}
```'''.strip()
        
        messages = [
            {"role": "system", "content": task},
            {"role": "user", "content": '// code:\n' + code},
            {"role": "user", "content": '// example usage:\n' + example_usage}
        ]

        for template, error in history:
            messages.append({"role": "assistant", "content": template.model_dump_json(indent=2)})
            messages.append({"role": "user", "content": f'// Fix the error:\n{error}'})

        s = self.query(messages, Template)

        return s


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
    template = llm.prompt_lifetime(code, object, constructor)
    print(template.template)


def usage(args):
    llm = LLM('gpt-4o-mini')
    code = open(args.code).read()
    stubs = Stubs.model_validate_json(open(args.stubs).read())
    stub = stubs.stubs[0]
    objects = Objects.model_validate_json(open(args.objects).read())
    example_usage = get_example_usage(args.codebase, args.function)

    usage = llm.prompt_usage(code, example_usage, args.function, stub, objects)
    print(usage.template)

    endpoint = stub.to_endpoint(args.function, usage.template, objects)
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

        usage = llm.prompt_usage(code, example_usage, args.function, stub, objects, history=history)
        print(usage.template)

        endpoint = stub.to_endpoint(args.function, usage.template, objects)
        res = try_build(endpoint, args.build_script, config=args.config)
        
        if res is None:
            print('Success!')
            break
        
        else:
            print('Build failed, retrying...')
            print('-' * 60)
            print(res.error)
            print('-' * 60)
            history.append((usage, TemplateError(error=res.error)))

def _usage_loop(llm, code, function, stub, objects, example_usage, build_script, config, max_attempts: int = 5) -> Optional[Endpoint]:
    history = []
    for i in range(max_attempts):
        print(f'Attempt {i + 1}...', end=' ')

        usage = llm.prompt_usage(code, example_usage, function, stub, objects, history=history)
        print(usage.template)
        endpoint = stub.to_endpoint(function, usage.template, objects)
        res = try_build(endpoint, build_script, config=config)
        
        if res is None:
            print('Success!')
            return endpoint
        else:
            print('Fail')
            print('-' * 60)
            print(res.error)
            print('-' * 60)
            history.append((usage, TemplateError(error=res.error)))

    return None

def _lifetime_loop(llm, code, object, objects, constructor, build_script, config, max_attempts: int = 5) -> Optional[Endpoint]:
    
    stub = Stub(object_args=[ObjectArg(name='obj', typ=object.name, dir='new' if constructor else 'del')])
    stub_name = f'{"construct" if constructor else "destruct"}_{object.name}'

    history = []
    for i in range(max_attempts):
        print(f'Attempt {i + 1}...', end=' ')

        template = llm.prompt_lifetime(code, object, constructor, history=history)
        print(template.template)
        endpoint = stub.to_endpoint(stub_name, template.template, objects)
        res = try_build(endpoint, build_script, config=config)
        
        if res is None:
            print('Success!')
            return endpoint
        else:
            print('Fail')
            print('-' * 60)
            print(res.error)
            print('-' * 60)
            history.append((template, TemplateError(error=res.error)))

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

    llm = LLM('gpt-4o-mini')
    objects = llm.prompt_objects(code)
    functions = llm.prompt_functions(code, objects)
    
    print(f'{len(objects.objects)} objects')
    print(f'{len(functions.functions)} functions')

    fstubs = []

    for function in functions.functions:
        example_usage = get_example_usage(codebase, function)
        stubs = llm.prompt_stubs(code, example_usage, function, objects)
        print(f' - {function} ({len(stubs.stubs)} stubs)')
        for stub in stubs.stubs:
            # print [(dir) type name, ...]
            print(f'[{", ".join([f"({o.dir}) {objects.find(o.typ).canonical_type} {o.name}" for o in stub.object_args])}]')
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
            if (object.name, constructor) in auto_lifetimes:
                print(f'Skipping {"constructor" if constructor else "destructor"} for {object.name} (auto-generated)')
                continue

            print(f'Generating {"constructor" if constructor else "destructor"} for {object.name}...')
            endpoint = _lifetime_loop(llm, code, object, objects, constructor, build_script, config)
            if endpoint is not None:
                endpoints.append(endpoint)

    for function, stub in fstubs:
        print(f'Generating usage for {function}...')
        endpoint = _usage_loop(llm, code, function, stub, objects, example_usage, build_script, config)
        if endpoint is not None:
            endpoints.append(endpoint)

    print(f'Generated {len(endpoints)} endpoints')
    
    schema = Schema(endpoints=endpoints)
    j = schema.model_dump_json(indent=2)

    schema.pprint()

    with open(args.output, 'w') as f:
        f.write(j)
