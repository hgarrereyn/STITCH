
from typing import List

from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from ..data.project_info import ProjectInfo
from ..data.metadata import FunctionStubTemplate, SchemaMetadata, FunctionStubMapping, FunctionStub, FunctionArgumentUsage, ObjectStub
from ..utils.colors import Colors
from ..utils.general import split_into_chunks
from ..build.validator import validate_function, format_and_check

BATCH_SIZE = 40


PROMPT_TEMPLATE = """
You are an expert C/C++ fuzzer engineer.

You will be provided a detailed list of functions found in a codebase.
Your task is to generate a `stub` for each function.
Generate a `stub` for each function, do not skip any functions, do not stop early.
Use the following guidelines:

[STUB_GUIDELINES]
A `stub` is a short snippet of C++ code that uses some `inputs`, invokes a particular function, and then initializes some `outputs`.
The `inputs` will be initialized before the stub runs (by the fuzzer), and the `outputs` should be initialized by the stub.
Any `inputs` that are marked as `delete` should be freed by the stub (or destructed during the function call).
All other arguments should be randomly generated, without violating the API contract of the function.

Specifically, the stub should:
1. Perform any necessary pre-validation of `inputs` to ensure they are not in an invalid state.
2. Initialize any necessary extra arguments (such as integers, or enum options) to valid (but random) values.
3. Construct any dummy callback functions as necessary using lambda functions. (e.g. `auto callback = [](int a, int b) {{ ... }}`)
4. Invoke the function, using the provided `inputs`, generated random arguments. (initializing `outputs` as applicable)
5. Ensure that the function did not return an error code.

Use the following macros:
- Invoke `FUZZ_BAIL()` if any of the `inputs` are invalid or the function returns an error code.
- Invoke `FUZZ_PARAM(type)` to generate a random value of the given type. (e.g. `FUZZ_PARAM(int)`, `FUZZ_PARAM(float[5])`)
    - `FUZZ_PARAM(T)` returns a newly allocated `T*`
    - e.g. `int x = *FUZZ_PARAM(int);` will allocate a new `int`.
    - Only use `FUZZ_PARAM` for fixed-size primitive types, or arrays of fixed-size primitive types.
    - Do not use `FUZZ_PARAM` for `struct` types, pointers, function pointers, or any other non-primitive types.
- Invoke `FUZZ_PARAM_STR(max_length)` to generate a random string up to the given length. (e.g. `FUZZ_PARAM_STR(10)`)
    - `max_length` must be a constant integer literal, e.g. `10`, not a variable or an expression.
    - The returned value is a `const char*` to a newly allocated string, it should not be freed by the stub.
- Invoke `FUZZ_PARAM_FILE(filesize)` to generate a filepath pointing to a file filled with random data of the given size.
    - `filesize` must be a constant integer literal, e.g. `10`, not a variable.
    - The returned value is a `const char*` to a newly allocated string, it should not be freed by the stub.
    - The file will be pre-filled with random data, and automatically cleaned up by the fuzzer.
- If you need to perform memory allocation/deallocation outside of the target library, use `gf_malloc`/`gf_free`.

On variables:
- Variables listed as `inputs` will be pre-defined and allocated by the fuzzer.
    - Imagine `<type> <name> = ...;` exists right before your stub code for every input variable.
- Variables listed as `outputs` will be pre-defined by the fuzzer, but not pointing to any valid memory.
    - Imagine `<type> <name>;` exists right before your stub code for every output variable.
- All other variables should be explicitly initialized by the stub.

The stub code will be injected into a function, therefore:
- Do not define any new functions (except for lambda callback functions)
- Do not use any global variables
- Do not use any static variables
- Do not use any directives (e.g. `#include`, `#define`)

Tips:
- Look at the input type and signature of the function carefully to determine if you need to dereference pointers (or increase the pointer level)
[/STUB_GUIDELINES]

[CODING_GUIDELINES]
At the top of your stub, write a comment listing the inputs and outputs of the function.

# Randomness
- Maximize the use of `FUZZ_PARAM` and `FUZZ_PARAM_STR`/`FUZZ_PARAM_FILE` to generate random values.
- If functions have optional arguments, generate a random boolean and use it to determine if the argument should be used.

# Filepaths
- If a function has an argument that appears to be a filepath, use `FUZZ_PARAM_FILE` (not `FUZZ_PARAM_STR`) to generate a random filepath.

# Variable size string (null-terminated)
If you need a variable size string, use `FUZZ_PARAM_STR(max_length)`:
```
// Generate a random name up to 256 characters
const char *name = FUZZ_PARAM_STR(256);
```

# Variable size buffer
If you need to pass both a buffer and a size argument, always use `FUZZ_PARAM_STR(max_length)`:
```
// Sample a size between 10 and 100
size_t length = *FUZZ_PARAM(size_t) % 90 + 10;

// Allocate a buffer with the maximum possible size
char *buffer = FUZZ_PARAM_STR(100);
```

# Array generation
- If you need a variable size array of fixed-size primitive types (e.g. 0 to 20 ints), you can use the following pattern:
```
size_t length = *FUZZ_PARAM(size_t) % 20;
int *array = (int *)gf_malloc(length * sizeof(int));
memcpy(array, FUZZ_PARAM(int[length]), length * sizeof(int));
```

# Selecting an option
If you need to select a random value from a list of options, you can use the following pattern (ensure use of `unsigned int`):
```
const char *options[] = {"foo", "bar", "baz"};
const char *option = options[*FUZZ_PARAM(unsigned int) % 3];
```

# Memory
- Ensure all memory accesses are bounds-checked.
- Ensure that all arguments passed to the function are defined in the main scope, not inside sub-scopes such as `if`/`else` statements.
[/CODING_GUIDELINES]

[AVOIDING_ERRORS]
It is critically important that you do not introduce any API errors into the stub.

Common errors to avoid:
- Do not try to use `FUZZ_PARAM` to obtain arbitrary pointers. If you need a pointer to something, it must be either passed as an input or you need to construct it in the stub.
- Ensure that all of the variables you use in the stub are either defined by you or passed as an input. There will be no other variables in scope.
[/AVOIDING_ERRORS]

[EXAMPLE]
<function>
{{
  "name": "initialize_foo",
  "signature": "int create_foo(struct ctx *ctx, struct foo **f, int id, char *name, char *backing_file, struct err **err)",
  "description": "Creates a new foo with a given id, name, and backing file.",
  "requirements": [
    "id must be a positive integer between 0 and 1000",
    "name must be a null-terminated string",
    "backing_file must be a null-terminated string",
    "f will be initialized to the newly allocated foo",
    "err will be initialized to an error (if any error occurs)"
  ],
  "inputs": [
    {{"name": "ctx", "type": "struct ctx *", "delete": false}},
  ],
  "outputs": [
    {{"name": "f", "type": "struct foo *"}}
  ]
}}
</function>
<generated_stub>
// Inputs: (struct ctx *ctx)
// Outputs: (struct foo *f)

// Validate inputs
if (!ctx) {{
    FUZZ_BAIL();
}}

// Initialize any extra arguments
int id = *FUZZ_PARAM(int) % 1000;

// Generate a random name up to 256 characters
const char *name = FUZZ_PARAM_STR(256);

const char *backing_file = FUZZ_PARAM_FILE(256);

// Required output that is not specified in the function description
struct err *err;

// Invoke the function
int ret = create_foo(ctx, &f, id, name, backing_file, &err);

// Ensure the function did not return an error code
if (ret != 0) {{
    FUZZ_BAIL();
}}
</generated_stub>
[/EXAMPLE]

[OBJECTS]
{objects}
[/OBJECTS]

[FUNCTIONS]
{functions}
[/FUNCTIONS]
"""


class StubInput(BaseModel):
    name: str
    type: str
    delete: bool

class StubOutput(BaseModel):
    name: str
    type: str

class FunctionInfo(BaseModel):
    name: str
    signature: str
    description: str
    requirements: List[str]
    inputs: List[StubInput]
    outputs: List[StubOutput]

    def to_function_proto(self, template: str) -> str:
        args = []
        for inp in self.inputs:
            args.append(f'{inp.type} {inp.name}')
        for out in self.outputs:
            args.append(f'{out.type} {out.name}')

        code = ''
        code += 'void test_function('
        code += ', '.join(args)
        code += ') {\n'
        code += template
        code += '\n}\n'
        return code

class TemplatesResponse(BaseModel):
    functions: List[FunctionStubTemplate]


def _create_function_info(objects: List[ObjectStub], func: FunctionStub, mapping: FunctionStubMapping) -> FunctionInfo:
    info = FunctionInfo(
        name=func.name,
        signature=func.signature,
        description=func.description,
        requirements=func.requirements,
        inputs=[],
        outputs=[]
    )

    for arg in [*mapping.arguments, mapping.return_value]:
        if arg.canonical_type == 'none':
            continue

        obj = next((x for x in objects if x.type == arg.canonical_type), None)
        if obj is None:
            raise ValueError(f'Object {arg.canonical_type} not found in objects')
        
        if obj.lifetime == 'ephemeral':
            continue

        if arg.usage == FunctionArgumentUsage.DEFAULT:
            info.inputs.append(StubInput(name=arg.name, type=arg.canonical_type, delete=False))
        elif arg.usage == FunctionArgumentUsage.NEW:
            info.outputs.append(StubOutput(name=arg.name, type=arg.canonical_type))
        elif arg.usage == FunctionArgumentUsage.DELETE:
            info.inputs.append(StubInput(name=arg.name, type=arg.canonical_type, delete=True))

    return info


def _validate_template(info: ProjectInfo, func: FunctionInfo, template: FunctionStubTemplate) -> bool:
    code = ''
    code += 'int main() {}\n'
    code += func.to_function_proto(template.stub)
    
    res = format_and_check(code, info, verbose=True, external=True)
    if res is not None:
        print(f'{Colors.RED}Invalid template for {Colors.YELLOW}{func.name}{Colors.END}:')
        print(res)
        return False
    else:
        print(f'{Colors.GREEN}Valid template for {Colors.YELLOW}{func.name}{Colors.END}:')
        print(code)
        return True


def _gen_templates(info: ProjectInfo, meta: SchemaMetadata, model: str) -> List[FunctionStubTemplate]:
    def log(msg: str):
        print(f'[{Colors.GREEN}TMPL{Colors.END}] {msg}')
    
    remaining_functions = {x.name: x for x in meta.functions}
    templates = []

    funcs = {x.name: x for x in meta.functions}
    mappings = {x.name: x for x in meta.mapping_functions}

    infos = []

    for name in funcs:
        if name not in mappings:
            log(f'{Colors.RED}Function {Colors.YELLOW}{name}{Colors.END} not found in mappings, skipping...')
            continue

        infos.append(_create_function_info(meta.objects, funcs[name], mappings[name]))

    infos_by_name = {x.name: x for x in infos}

    log(f'Generating {Colors.YELLOW}{len(infos)}{Colors.END} templates...')

    for idx in range(0, len(infos), BATCH_SIZE):
        batch = infos[idx:idx+BATCH_SIZE]
        log(f'Generating batch {idx // BATCH_SIZE + 1} of {((len(infos) - 1) // BATCH_SIZE) + 1}...')

        llm = ChatOpenAI(model=model).with_structured_output(TemplatesResponse, strict=True)
        res: TemplatesResponse = llm.invoke(PROMPT_TEMPLATE.format(objects=meta.objects, functions=batch))

        for template in res.functions:
            if template.name not in remaining_functions:
                log(f'{Colors.RED}Function {Colors.YELLOW}{template.name}{Colors.END} not found in metadata, skipping...')
                continue

            if not _validate_template(info, infos_by_name[template.name], template):
                continue

            templates.append(template)
            remaining_functions.pop(template.name)

    log(f'{Colors.GREEN}Generated {Colors.YELLOW}{len(templates)}{Colors.END} templates...')
    log(f'{Colors.RED}Failed to generate {Colors.YELLOW}{len(remaining_functions)}{Colors.END} templates...')
    return templates


def main(args):
    info = ProjectInfo.model_validate_json(open(args.info).read())
    meta = SchemaMetadata.model_validate_json(open(args.meta).read())

    templates = _gen_templates(info, meta, args.model)
    print(templates)

    meta.template_functions = templates
    if args.output:
        with open(args.output, 'w') as f:
            f.write(meta.model_dump_json(indent=2))
    else:
        print(meta.model_dump_json(indent=2))


def register(subparsers):
    parser = subparsers.add_parser('gen-templates', help='Generate templates for the codebase')
    parser.add_argument('--info', type=str, default='info.json', help='Path to the project info file')
    parser.add_argument('--meta', type=str, required=True, help='Path to the schema metadata file')
    parser.add_argument('--model', type=str, default='gpt-4.1-mini', help='Model to use for scanning')
    parser.add_argument('--output', type=str, default=None, help='Path to the output file')
    parser.set_defaults(func=main)
