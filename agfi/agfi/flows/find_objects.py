from typing import List, Optional

from langchain_core.callbacks import get_usage_metadata_callback
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from ..data.project_info import ProjectInfo
from ..data.metadata import ObjectStub, SchemaMetadata, UsageTracker, UsageMetrics
from ..utils.colors import Colors
from ..build.validator import validate_object
from ..utils.general import split_into_chunks, merge_headers


PROMPT_SCAN_CHUNK = """
You are an expert C/C++ developer.

Your task is to examine the following chunk of C/C++ code and find all the different custom object types defined in the code.
For each custom object, generate a detailed description, following the guidelines below.

[OBJECT_GUIDELINES]

An `object` is any public-facing custom type that is used to curry context or data across function calls.

We distinguish between objects that are `persistent` and those that are `ephemeral`.
- `persistent` objects are created and used in multiple function calls (for example, a `struct foo *` that is allocated once and used in multiple function calls, or a `foo_id_t` that is used in multiple function calls)
- `ephemeral` objects are used for a single function call, and often managed by the caller (for example, an `enum flag_t` that is passed to a function and not used after the function call returns, or a `handler_t` function pointer that is used for a single function call)

Object types can be any of:
- custom typedefs (e.g. `typedef int myid_t`)
- custom enums (e.g. `enum myenum {{ MYVAL1, MYVAL2 }}`)
- structs (e.g. `struct mystruct {{ int a; int b; }}`)
- classes (e.g. `class myclass {{ int a; int b; }}`)
- etc...

Instructions:
1. Scan the code and identify all the different object types.
2. For each object, determine whether it is `persistent` or `ephemeral`. (hint: look at functions that use the object)
3. Collect the following information for each object:
    - `type`: See [CANONICAL_TYPE_GUIDELINES]
    - `aliases`: a list of aliases for the object (e.g. if the object is `struct foo *`, aliases might include `foo *`, `FOO_PTR`, `FOO_P`, etc...)
    - `description`: a detailed description of the object
    - `lifetime`: whether the object is `persistent` or `ephemeral`
    - `definition`: the code definition of the object, i.e. for non-opaque types, provide the full code that can be used to instantiate the object.

In the description, make sure to address the following points:
- What is the purpose of the object?
- How is the object instantiated? I.e. through an API call, directly in user code, etc.
- How should the object be cleaned up? I.e. what functions should be called to free the object?

[/OBJECT_GUIDELINES]

[CANONICAL_TYPE_GUIDELINES]
The `type` field of the object should be a fully-qualified C/C++ type that can be used to declare a variable of that type.

Be careful with the following cases:

1. Opaque structs
```c
struct foo;
```
Use the pointer form: `struct foo *` as the raw form cannot be used to declare a variable.

2. Anonymous enums/unions with a typedef
```c
typedef enum {{ int a; int b; }} myenum_t;
```
Use the typedef name: `myenum_t`.

3. Typedefs
```c
typedef int myid_t;
```
Use the typedef name: `myid_t`.

4. Function pointer types
```c
typedef void (*myfunc_t)(int, int);
```
Use the typedef name: `myfunc_t` instead of the function pointer syntax (`void (*)(int, int)`).

[/CANONICAL_TYPE_GUIDELINES]

[EXAMPLE]
<code>
typedef int myid_t;

// Used to specify parameters for bar creation
struct foo {{
    int a;
    int b;
}};

// enum representing the type of a bar
typedef enum bar_type {{
    BAR_TYPE_A,
    BAR_TYPE_B,
}} bar_type_t;

// Represents a bar.
// Only one bar can exist at a time.
struct bar;

typedef struct bar bar_t;
typedef struct bar *bar_p;

bar_p bar_create(struct foo *foo, bar_type_t type);
void bar_destroy(bar_p bar);
</code>
<objects>
[
    {{
        "type": "struct foo *",
        "aliases": ["struct foo"],
        "description": "Struct used to specify parameters for bar creation. Contains two integers. Managed by the caller.",
        "lifetime": "ephemeral",
        "definition": "struct foo {{ int a; int b; }};"
    }},
    {{
        "type": "bar_type_t",
        "aliases": ["enum bar_type"],
        "description": "Enum representing the type of a bar. Used to specify the type of a bar when creating it.",
        "lifetime": "ephemeral",
        "definition": "enum bar_type {{ BAR_TYPE_A, BAR_TYPE_B }};"
    }},
    {{
        "type": "struct bar *",
        "aliases": ["struct bar", "bar_t", "bar_p"],
        "description": "Opaque pointer to a bar. Only one bar can exist at a time. Created by `bar_create` and destroyed by `bar_destroy`.",
        "lifetime": "persistent",
        "definition": "struct bar;"
    }}
]
</objects>
[/EXAMPLE]

[CODE]
{code}
[/CODE]
"""


class ObjectsResponse(BaseModel):
    objects: List[ObjectStub]


def _scan_chunk(info: ProjectInfo, model: str, chunk: str) -> List[ObjectStub]:
    llm = ChatOpenAI(model=model).with_structured_output(ObjectsResponse, strict=True)
    res: ObjectsResponse = llm.invoke(PROMPT_SCAN_CHUNK.format(code=chunk))
    return res.objects


# def _validate_object(info: ProjectInfo, object: ObjectStub) -> bool:
#     """Ensure we can instantiate this object in code."""
#     return validate_object(object.type, info, True) is None


def _find_valid_object_type(info: ProjectInfo, object: ObjectStub) -> Optional[str]:
    """Find a valid object type for the given object."""
    if validate_object(object.type, info, True) is None:
        return object.type
    
    # Check if any of the aliases are valid
    for alias in object.aliases:
        if validate_object(alias, info, True) is None:
            return alias

    return None


def find_objects(info: ProjectInfo, model: str, headers: List[str], usage: UsageTracker) -> List[ObjectStub]:
    def log(msg: str):
        print(f'[{Colors.BOLD}{Colors.GREEN}FIND{Colors.END}] {msg}')

    log('Merging headers...')
    merged_headers = merge_headers(headers)
    log(f'Merged {len(headers)} headers into {len(merged_headers)} lines')

    log('Scanning merged headers...')
    with get_usage_metadata_callback() as cb:
        objects = _scan_chunk(info, model, merged_headers)
        usage.record('find-objects', UsageMetrics.from_usage(cb.usage_metadata))

    log(f'Found {len(objects)} objects')

    # Merge objects with the same type
    merged_objects = {}
    for obj in objects:
        if obj.type not in merged_objects:
            merged_objects[obj.type] = obj
        else:
            merged_objects[obj.type].aliases.extend(obj.aliases)

    log(f'Merged {len(objects)} objects into {len(merged_objects)} unique objects')
    objects = list(merged_objects.values())

    log('Validating objects...')
    valid_objects = []
    for obj in objects:
        vtype = _find_valid_object_type(info, obj)
        if vtype is not None:
            obj.type = vtype
            valid_objects.append(obj)
            log(f'Check {obj.type}: {Colors.GREEN}VALID{Colors.END}')
        else:
            log(f'Check {obj.type}: {Colors.RED}INVALID{Colors.END}')

    log(f'Found {len(valid_objects)} valid objects')
    return valid_objects


def main(args):
    info = ProjectInfo.model_validate_json(open(args.info).read())
    objects = find_objects(info, args.model, args.headers)
    
    # Write to file if provided
    meta = SchemaMetadata(objects=objects)
    if args.output:
        with open(args.output, 'w') as f:
            f.write(meta.model_dump_json(indent=2))
    else:
        print(meta.model_dump_json(indent=2))


def register(subparsers):
    parser = subparsers.add_parser('find-objects', help='Find objects in the codebase')
    parser.add_argument('--info', type=str, default='info.json', help='Path to the project info file')
    parser.add_argument('--headers', type=str, nargs='+', required=True, help='List of header files to scan')
    parser.add_argument('--model', type=str, default='gpt-4.1-mini', help='Model to use for scanning')
    parser.add_argument('--output', type=str, default=None, help='Path to the output file')
    parser.set_defaults(func=main)
