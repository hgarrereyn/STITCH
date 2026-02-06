[CODE_BLOCKS]
A code block is a concise snippet of C++ code that invokes a particular function in some target API without violating any of the rules or restrictions.

A code block may require objects as inputs (for example objects created in other code blocks) and it may produce objects as outputs (which can be used in subsequent code blocks).
Inside the code block you have access to several macros which allow you to sample random parameters.
As much as possible, you should use random (yet valid!) parameters for function arguments. See [SPECIAL_FUNCTIONS] for more details.

You must explicitly define the inputs and outputs your code block accepts. A code block takes exclusive ownership of its inputs and releases ownership when it produces outputs. For example, if a code block frees one of its inputs, that variable cannot be an output. While a list of codebase objects is provided in [OBJECTS], only the types specified in [AVAILABLE_INOUT_OBJECTS] are available as inputs/outputs. If you need other objects to invoke your target function, you can construct it yourself with `FUZZ_PARAM` macros (see [SPECIAL_FUNCTIONS]) or dereference container objects that are available as inputs.

The purpose of the code block is to fuzz-test the target function. If you are unable to figure out suitable inputs and outputs, synthesize as much code as possible to cover the target function by constructing objects yourself.

Each code block is a JSON object with the following fields:
- `name`: name of the target function (verbatim from the input)
- `code`: the code block (in C++), with no function signature, just raw code
- `inputs`: the inputs accepted by the code block
- `outputs`: the outputs produced by the code block
[/CODE_BLOCKS]

[SCOPE_AND_CONSTRAINTS]
Each code block will be injected into a function inside a C++ file.
Input variables will be pre-initialized and output variables will be copied back out by the fuzzer runtime.

Ensure that your implementation is fully self-contained and can be compiled inside a single C/C++ scope.
Do not define any global variables, macros, functions, etc...
Do not `throw`, `exit`, or `abort` (instead use `FUZZ_BAIL()`).
Do not use the `return` keyword. If you need conditional execution, use the pattern: `if (condition) { <code> }` instead of `if (!condition) { return; } <code>`.

If you need to pass function pointers for e.g. callbacks, you can use lambda functions. For example:
```cpp
auto callback = [](int a, int b) {
    // ...
};
use_foo(callback);
```
However, since these are only valid in the scope of the code block, ensure that the function pointer does not escape the code block.
[/SCOPE_AND_CONSTRAINTS]

[SPECIAL_FUNCTIONS]
You have access to the following special functions:

## Indicators
- `void FUZZ_BAIL()`: should be invoked to mark an error case (for example an error return value from the target API function). This function does not return, it will cause the current test case to terminate. It is not necessary to clean up any resources before calling this function.

## Random Sampling
- `const T *FUZZ_PARAM(T)`: samples a random value of type `T` returning a `const T *`, where `T` is either a primitive type, or a fixed-size struct containing only primitive types, or a fixed-size array of primitive types. The returned value should not be freed. Examples: `int x = *FUZZ_PARAM(int)`. Note: never try to fetch a pointer type from `FUZZ_PARAM` since this will give you random pointer values which will lead to crashes. If you need a pointer type, it must either be an input object or you need to construct it yourself. Cast the result if constructing array types, e.g. `int *arr = (int *)FUZZ_PARAM(int[10])`. Do **not** use `FUZZ_PARAM` to construct an object that is provided in [AVAILABLE_INOUT_OBJECTS], instead request that object as an input.
- `const char *FUZZ_PARAM_STR(N)`: samples a random string of size `N`, where `N` must be an integer literal (not an expression). It returns a `const char *` to the string, which should not be freed. Examples: `const char *s = FUZZ_PARAM_STR(10)`.
- `const char *FUZZ_PARAM_FILE()`: samples a random filename pointing to a file filled with random bytes. Use this for fuzzable arguments that expect file paths. Examples: `const char *path = FUZZ_PARAM_FILE()`.

## Limitations
Your code block will be synthesized into a C++ file, in this process the `FUZZ_PARAM_*` macros will be replaced with actual fuzzer values.
Invoking the same `FUZZ_PARAM_*` macro multiple times will return the same value.
In order to avoid this, use `FUZZ_PARAM_*` to allocate an array and iterate over it.
The array size must be a constant integer literal (not an expression), so pick the maximum size you will need if the loop is not fixed.
I.e. instead of:
```cpp
for (int i = 0; i < 10; i++) {
    // This will return the same value for each iteration
    arr[i] = *FUZZ_PARAM(int);
}
```
you should do:
```cpp
// Request a fixed-size backing array of random values
int *backing = (int *)FUZZ_PARAM(int[10]);
for (int i = 0; i < 10; i++) {
    arr[i] = backing[i];
}
```
Additionally, `FUZZ_PARAM_*` macros can currently *not* be used inside lambda functions.

## Value constraints
- Often it is useful to sample a certain range of values for an argument. For example, if an argument is an integer, you can sample a value between 10 and 100 with `*FUZZ_PARAM(unsigned int) % 90 + 10`.
- If you need to sample a value from a list of options, you can use the following pattern (ensure use of `unsigned int`):
```cpp
const char *options[] = {{"foo", "bar", "baz"}};
int num_options = sizeof(options) / sizeof(options[0]);
const char *option = options[*FUZZ_PARAM(unsigned int) % num_options];
```
- If you need to sample a parameter that can e.g. be null, you can use the following pattern:
```cpp
const char *nullable_string = ( *FUZZ_PARAM(unsigned int) % 2 == 0 ) ? NULL : FUZZ_PARAM_STR(10);
```

## Dynamic validation
If you need to perform dynamic validation of inputs and outputs (beyond just the type), you can attach metadata (strings or integers) to input/output variables.
These attributes persist across code blocks.

- `void FUZZ_SET_ATTR_INT(<variable>, const char *key, int value)`: sets an integer attribute on a variable.
- `void FUZZ_SET_ATTR_STR(<variable>, const char *key, const char *value)`: sets a string attribute on a variable.
- `int FUZZ_GET_ATTR_INT(<variable>, const char *key)`: gets an integer attribute from a variable.
- `const char *FUZZ_GET_ATTR_STR(<variable>, const char *key)`: gets a string attribute from a variable.

For example, if you want to track an identifier used to initialize a variable, you can do:
```cpp
int id = *FUZZ_PARAM(int);
foo *f = target_api_create_foo(id);
FUZZ_SET_ATTR_INT(f, "id", id); // Store a fuzzer-level attribute
```

A subsequent code block could then read the attribute and take action based on the value:
```cpp
int id = FUZZ_GET_ATTR_INT(f, "id");
if (id != 0) {
    // ...
}
```

## Global state validation
You can also attach metadata to the global state, which similarly persists across code blocks but is not tied to any particular variable.

- `void FUZZ_SET_ATTR_INT_GLOBAL(const char *key, int value)`: sets an integer attribute on the global state.
- `void FUZZ_SET_ATTR_STR_GLOBAL(const char *key, const char *value)`: sets a string attribute on the global state.
- `int FUZZ_GET_ATTR_INT_GLOBAL(const char *key)`: gets an integer attribute from the global state.
- `const char *FUZZ_GET_ATTR_STR_GLOBAL(const char *key)`: gets a string attribute from the global state.

For example, if you want to track whether a function was called, you can do:
```cpp
foo();
FUZZ_SET_ATTR_INT_GLOBAL("foo_called", 1);
```
A subsequent code block could then read the attribute and take action based on the value:
```cpp
if (FUZZ_GET_ATTR_INT_GLOBAL("foo_called") == 1) {
    // ...
}
```
[/SPECIAL_FUNCTIONS]

[INPUTS_OUTPUTS]
Each of `inputs` and `outputs` should be a JSON list of objects with the following fields:
- `name`: specifies the variable name as used inside your code block
- `type`: must match one of the types in [AVAILABLE_INOUT_OBJECTS]

## Inputs
Input variables will be pre-initialized with the type you specify (the fuzzer will invoke a different code block to produce the type beforehand).
You can reference inputs directly in your code block, and you should not try to redefine that variable name.
Only types specified in [AVAILABLE_INOUT_OBJECTS] can be used as inputs and outputs.
If you need a different object type, you must either construct it yourself (using several `FUZZ_PARAM` calls), or if it is owned by another object, you can request that object as an input and then dereference it.

## Outputs
Output variables should be initialized by your code block.
After your code block terminates, whichever variables you specify will be used as outputs.
These can share variable names with inputs (a common pattern is to use the same variable name for an input which is used in the code block, and then available as an output).
Output variables must not point to any data that is local to the code block, as this will cause out-of-scope issues.
Outputs should only be objects defined in [AVAILABLE_INOUT_OBJECTS], they cannot be primitive types.

## When to use Inputs/Outputs
Carefully read the object descriptions to understand how to obtain an instance of it.
- If the object type is in [AVAILABLE_INOUT_OBJECTS], you can request an input variable of that type and use it directly:
```json
{
    "objects": [{"type": "foo *", "aliases": ["foo"], "description": "A foo object managed by the API", "definition": "struct foo { int a; int b; };", "direct_api_constructors": ["foo_create"], "owned_by": []}],
    "available_inout_objects": ["foo *"]
}
```
```json
{
    "name": "use_foo",
    "inputs": [
        {"name": "foo", "type": "foo *"}
    ],
    "outputs": [
        {"name": "foo", "type": "foo *"}
    ],
    "code": "use_foo(foo);"
}
```
- If the object is not in [AVAILABLE_INOUT_OBJECTS] and is not owned by another object, you must construct it directly in your template and clean it up (if allocated on the heap):
```json
{
    "objects": [{"type": "foo *", "aliases": ["foo"], "description": "A foo object created by the user", "definition": "struct foo { int a; int b; };", "direct_api_constructors": [], "owned_by": []}],
    "available_inout_objects": []
}
```
```json
{
    "name": "use_foo",
    "inputs": [],
    "outputs": [],
    "code": "foo f = { .a = *FUZZ_PARAM(int), .b = *FUZZ_PARAM(int) };\nuse_foo(f);"
}
```
- If the object is not in [AVAILABLE_INOUT_OBJECTS] but is owned by another object, you can request that other object as an input (assuming it is in [AVAILABLE_INOUT_OBJECTS]) and then dereference it.
```json
{
    "objects": [
        {"type": "bar *", "description": "A bar object managed by the API", "definition": "struct bar { foo *child_foo; };", "direct_api_constructors": [], "owned_by": []},
        {"type": "foo *", "description": "A foo object owned by a bar. Not directly created by the API.", "definition": "struct foo { int a; int b; };", "direct_api_constructors": [], "owned_by": ["bar *"]}
    ],
    "available_inout_objects": ["bar *"]
}
```
```json
{
    "name": "use_foo",
    "inputs": [
        {"name": "bar", "type": "bar *"}
    ],
    "outputs": [
        {"name": "bar", "type": "bar *"}
    ],
    "code": "use_foo(bar->child_foo);"
}
```

## Important notes
If your code block takes inputs and does not delete or free them, you must also mark them as outputs!
Make sure your outputs are stored on the heap, not the stack.

## Handling unknown or untracked types
If you need to handle a type that is not provided in [OBJECTS], you have two options:

1. (preferred) Construct the object yourself using the `FUZZ_PARAM` macros and ensure that it is cleaned up inside the code block. For example, if a function operates on a mutable `char *`, you can construct the buffer yourself and then free it after the function call.

2. (rare) Occasionally functions are defined to operate on raw pointers with special constraints (e.g. custom library malloc/free implementations). If you need to pass raw pointers between code blocks, you can specify `void *` as an input or output type. However, you must enforce some external validation on the pointer (via the `FUZZ_SET_ATTR_INT`/`FUZZ_SET_ATTR_STR` macros). Do not blindly accept `void *` as an input without validating that it has the correct attributes (as determined by some other code block which produces it). Do not use `void *` as a general workaround for missing object types in [AVAILABLE_INOUT_OBJECTS].

Example of valid use of `void *` to discern between two different types:

Library code:
```cpp
// buffer1 can only be used with use_custom_buffer1
void *create_custom_buffer1();
void use_custom_buffer1(void *buffer);

// buffer2 can only be used with use_custom_buffer2
void *create_custom_buffer2();
void use_custom_buffer2(void *buffer);
```

Object definitions:
```json
{
    "objects": [],
    "available_inout_objects": []
}
```

Code blocks:
```json
[
    {
        "name": "create_custom_buffer1",
        "inputs": [],
        "outputs": [
            {"name": "buffer", "type": "void *"}
        ],
        "code": "void *buffer = create_custom_buffer1();\nFUZZ_SET_ATTR_INT(buffer, \"is_custom_buffer1\", 1);"
    },
    {
        "name": "use_custom_buffer1",
        "inputs": [
            {"name": "buffer", "type": "void *"}
        ],
        "outputs": [
            {"name": "buffer", "type": "void *"}
        ],
        "code": "if (FUZZ_GET_ATTR_INT(buffer, \"is_custom_buffer1\") != 1) {\n    FUZZ_BAIL();\n}\nuse_custom_buffer1(buffer);"
    },
    {
        "name": "create_custom_buffer2",
        "inputs": [],
        "outputs": [
            {"name": "buffer", "type": "void *"}
        ],
        "code": "void *buffer = create_custom_buffer2();\nFUZZ_SET_ATTR_INT(buffer, \"is_custom_buffer2\", 1);"
    },
    {
        "name": "use_custom_buffer2",
        "inputs": [
            {"name": "buffer", "type": "void *"}
        ],
        "outputs": [
            {"name": "buffer", "type": "void *"}
        ],
        "code": "if (FUZZ_GET_ATTR_INT(buffer, \"is_custom_buffer2\") != 1) {\n    FUZZ_BAIL();\n}\nuse_custom_buffer2(buffer);"
    }
]
```
[/INPUTS_OUTPUTS]

[CODING_STYLE]
## Memory allocation
The code you are writing will be used in a fuzzer.
Avoid allocating large regions of memory as this will slow fuzzing down.
Stick to allocating small regions of memory (<= 2048 bytes).

## Requirement specification
Leave comments in the code block that indicate which requirements are enforced (or unable to be enforced).
Style "// (REQ) ..." for enforced requirements and "// (REQ-TODO) ..." for requirements that were not possible to enforce.
[/CODING_STYLE]
