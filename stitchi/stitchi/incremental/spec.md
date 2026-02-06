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
- `type`: must match one of the types in [AVAILABLE_INOUT_OBJECTS] or `void *`

## Inputs
Input variables will be pre-initialized with the type you specify (the fuzzer will invoke a different code block to produce the type beforehand).
You can reference inputs directly in your code block, and you should not redefine that variable name.
Only types specified in [AVAILABLE_INOUT_OBJECTS] and `void *` (see [OBJECT_DECISION_CHECKLIST] EX3) can be used as inputs and outputs.
If you need a different object type, you must either construct it yourself (using several `FUZZ_PARAM` calls), or if it is owned by another object, you can request that object as an input and then dereference it.

## Outputs
Output variables should be initialized by your code block.
After your code block terminates, whichever variables you specify will be used as outputs.
All inputs that are not consumed or deleted must be marked as outputs!
These can share variable names with inputs (a common pattern is to use the same variable name for an input which is used in the code block, and then available as an output).
Output variables must not point to any data that is local to the code block, as this will cause out-of-scope issues.
Outputs should only be objects defined in [AVAILABLE_INOUT_OBJECTS] and `void *` (see [OBJECT_DECISION_CHECKLIST] EX3), they cannot be primitive types.
Newly produced outputs must be stored on the heap, not the stack.
To decide whether to specify an output, follow the [OUTPUT_DECISION_CHECKLIST].
[/INPUTS_OUTPUTS]

[OBJECT_DECISION_CHECKLIST]
Carefully read the object and function descriptions to understand what objects are provided by the library and how they should be used.

If you need an object of type `X` for the function `F`, follow the following checklist:
- Does `F` initialize the object `X`?
    - Yes: do not request `X` as an input, initialize it using `F` or follow the requirements in the [OBJECTS] section
- Is `X` in [AVAILABLE_INOUT_OBJECTS]?
    - Yes: use `X` as the input/output type (EX1)
- Is `X` a field in another object that is in [AVAILABLE_INOUT_OBJECTS]? (i.e. does its `owned_by` field contain `Y`?)
    - Yes: use `Y` as the input/output type and dereference it inside the code block (`Y->x`); do not save or return any new references to `Y->x` as this will cause a UAF (EX2)
- Is `X` an opaque pointer (i.e. `void *` or `HANDLE`) that is managed by the library?
    - Yes: use `void *` as the input type, check a constraining attribute before using it (i.e. `FUZZ_SET_ATTR_INT(void_ptr, "is_X", 1)`) or set the attribute if creating it (EX3)
- Is `X` an unconstrained fixed-size primitive type?
    - Yes: obtain `X` with `FUZZ_PARAM`
- Is `X` a constrained fixed-size primitive type?
    - Yes: obtain `X` with a modified call to `FUZZ_PARAM` that enforces some constraint (such as a range or a list of options)
- Is `X` a `char *` which should be a string literal?
    - Yes: obtain `X` with `FUZZ_PARAM_STR`
- Is `X` a `char *` which should be a file path?
    - Yes: obtain `X` with `FUZZ_PARAM_FILE`
- Is `X` a structure managed by the user? (i.e. an ephemeral `options` struct or similar?)
    - Yes: construct the object yourself (using several `FUZZ_PARAM` calls) and ensure that it is cleaned up inside the code block
- Is `X` a function pointer needed only inside the code block? (i.e. not something that will be stored in an object)
    - Yes: use a lambda function to construct the function pointer
- Is `X` a function pointer needed to be stored in an object?
    - Yes: avoid using a lambda function, pass `nullptr` if it is explicitly allowed, otherwise stub out the functionality
- Is `X` any other type?
    - Yes: avoid using it, it is not supported. Stub out the functionality or pass `nullptr` if it is explicitly allowed.

Examples of several cases are provided below:
<EX1>
Input:
```json
{
    "objects": [{"type": "foo *", "aliases": ["foo"], "description": "A foo object managed by the API", "definition": "struct foo { int a; int b; };", "direct_api_constructors": ["foo_create"], "owned_by": []}],
    "available_inout_objects": ["foo *"],
    "functions": [
        {
            "name": "use_foo", 
            "signature": "void use_foo(foo *foo)",
            "description": "Uses a foo object",
            "requirements": ["foo must be non-null"]
        }
    ]
}
```
Output:
```json
[
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
]
```
</EX1>
<EX2>
Input:
```json
{
    "objects": [
        {"type": "bar *", "aliases": ["bar"], "description": "A bar object managed by the API", "definition": "struct bar { foo *child_foo; };", "direct_api_constructors": ["bar_create"], "owned_by": []},
        {"type": "foo *", "aliases": ["foo"], "description": "A foo object owned by a bar. Not directly created by the API", "definition": "struct foo { int a; int b; };", "direct_api_constructors": [], "owned_by": ["bar *"]}
    ],
    "available_inout_objects": ["bar *"],
    "functions": [
        {
            "name": "use_foo", 
            "signature": "void use_foo(foo *foo)",
            "description": "Uses a foo object",
            "requirements": ["foo must be non-null"]
        }
    ]
}
```
Output:
```json
[
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
]
```
</EX2>
<EX3>
Input:
```json
{
    "objects": [],
    "available_inout_objects": [],
    "functions": [
        {
            "name": "create_custom_buffer", 
            "signature": "void *create_custom_buffer(const char *buffer_name)",
            "description": "Creates a new custom buffer",
            "requirements": ["custom_buffer must be freed by `free_custom_buffer` after use", "buffer_name must be a non-null string"]
        },
        {
            "name": "use_custom_buffer", 
            "signature": "void use_custom_buffer(void *custom_buffer)",
            "description": "Uses a custom buffer",
            "requirements": ["custom_buffer must be non-null", "custom_buffer must be a custom buffer created by the API"]
        },
        {
            "name": "free_custom_buffer", 
            "signature": "void free_custom_buffer(void *custom_buffer)",
            "description": "Frees a custom buffer",
            "requirements": ["custom_buffer must be a custom buffer created by the API"]
        }
    ]
}
```
Output:
```json
[
    {
        "name": "create_custom_buffer",
        "inputs": [],
        "outputs": [
            {"name": "buffer", "type": "void *"}
        ],
        "code": "const char *name = FUZZ_PARAM_STR(32);\nvoid *buffer = create_custom_buffer(name);\nFUZZ_SET_ATTR_STR(buffer, \"buffer_name\", name);\nFUZZ_SET_ATTR_INT(buffer, \"is_custom_buffer\", 1);"
    },
    {
        "name": "use_custom_buffer",
        "inputs": [
            {"name": "buffer", "type": "void *"}
        ],
        "outputs": [
            {"name": "buffer", "type": "void *"}
        ],
        "code": "if (FUZZ_GET_ATTR_INT(buffer, \"is_custom_buffer\") != 1) {\n    FUZZ_BAIL();\n}\nuse_custom_buffer(buffer);"
    },
    {
        "name": "free_custom_buffer", 
        "inputs": [
            {"name": "buffer", "type": "void *"}
        ],
        "outputs": [],
        "code": "if (FUZZ_GET_ATTR_INT(buffer, \"is_custom_buffer\") != 1) {\n    FUZZ_BAIL();\n}\nfree_custom_buffer(buffer);"
    }
]
```
</EX3>
[/OBJECT_DECISION_CHECKLIST]

[OUTPUT_DECISION_CHECKLIST]
When deciding whether to specify a variable as an output, consider the following cases:

If you have a variable of type `X` at the end of your code block:
- Was `X` an input variable that was consumed or deleted or freed?
    - Yes: do not specify `X` as an output
- Was `X` an input variable that was not consumed or deleted or freed?
    - Yes: specify `X` as an output of the same type
- Was `X` constructed inside the code block and listed in [AVAILABLE_INOUT_OBJECTS]?
    - Yes: specify `X` as an output (make sure it is stored on the heap) (EX4)
- Was `X` constructed inside the code block and has type `void *` or similar (e.g. `HANDLE`) *and* is intended to be consumed by other API functions?
    - Yes: add an attribute to `X` to indicate that it is a custom buffer (e.g. `FUZZ_SET_ATTR_STR(buffer, "is_custom_buffer", "1")`) and specify `X` as an output with type `void *`
- In all other cases: do not specify `X` as an output, make sure it is cleaned up inside the code block

Examples of several cases are provided below:
<EX4>
Input:
```json
{
    "objects": [{"type": "foo *", "aliases": ["foo"], "description": "A foo object managed by the API", "definition": "struct foo { int a; int b; };", "direct_api_constructors": ["init_foo"], "owned_by": []}],
    "available_inout_objects": ["foo *"],
    "functions": [
        {
            "name": "init_foo", 
            "signature": "void init_foo(foo **foo)",
            "description": "Initializes a foo object",
            "requirements": ["foo must be non-null and zero initialized"]
        }
    ]
}
```
Output:
```json
[
    {
        "name": "init_foo",
        "inputs": [],
        "outputs": [
            {"name": "foo", "type": "foo *"}
        ],
        "code": "foo *foo = (foo *)calloc(1, sizeof(foo));\ninit_foo(&foo);"
    }
]
```
</EX4>
[/OUTPUT_DECISION_CHECKLIST]

[CODING_STYLE]
## Memory allocation
The code you are writing will be used in a fuzzer.
Avoid allocating large regions of memory as this will slow fuzzing down.
Stick to allocating small regions of memory (<= 2048 bytes).

## Requirement specification
Leave comments in the code block that indicate which requirements are enforced (or unable to be enforced).
Style "// (REQ) ..." for enforced requirements and "// (REQ-TODO) ..." for requirements that were not possible to enforce.
Annotate each argument with a comment that indicates how it is constructed (input, synthesized, null, etc...) and whether or not that is spec-compliant.

## Safety
Do not perform any actions that could lead to crashes or other undefined behavior.
Do not perform casting between pointer types or incompatible types.
Do not invoke target APIs with invalid or malformed inputs.
Do not provide null pointers as inputs unless explicitly allowed by the spec (annotate this with a comment).
[/CODING_STYLE]
