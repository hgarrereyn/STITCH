from typing import List

# from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from agf.data.project_info import ProjectInfo
from agf.data.metadata import ObjectInfo, SchemaMetadata
from agf.utils.colors import Colors
from agf.build.validator import validate_object
from agf.utils.general import split_into_chunks


PROMPT_SCAN_CHUNK = """
You are an expert C/C++ developer.

Examine the following chunk of C/C++ code and find all the custom object types defined in the code.

An object type is any custom type that exists beyond a single function call and stores context or data such as:
- identifiers (e.g. `myid_t`)
- custom enums (e.g. `myenum_t`)
- struct pointers (e.g. `struct foo *`)
- class instances (e.g. `class::bar`)

Return a list of objects with the following fields:
- `typ`: the type of the object (e.g. `myid_t`, `myenum_t`, `struct foo *`, `class::bar`)
- `description`: a short description of the object
- `aliases`: a list of aliases for the object (e.g. if the object is `struct foo *`, aliases might include `foo *`, `FOO_PTR`, `FOO_P`, etc...)
- `constructed_by`: a list of functions that construct the object
- `destructed_by`: a list of functions that destruct the object
- `possible_states`: a list of possible states the object can be in
- `ghost_attributes`: a list of ghost attributes that describe the object

An object state represents the possible forms the object can be in.
- For a mutex, this could be "locked", "unlocked".
- For a type with multiple subtypes, this could be "type_a", "type_b", "type_c".
- For a type that can be in multiple states, this could be "initialized", "prepared", "finalized".
- Etc.

A ghost attribute is an attribute that is not necessarily stored in the object, but is used to describe the object.
- For example, a mutex might have a ghost attribute `locked` that is true if the mutex is locked and false otherwise.
- An object might have a ghost attribute `refcount` that describes the number of references to the object. (this may also be stored in the object)
- If an object has multiple states, this should also be a ghost attribute "state"

Find all ghost attributes that would be useful to reason about how the object can be used, and which API calls can change the state of the object.

Each ghost attribute should be specified as:
- `name`: the name of the ghost attribute
- `description`: a short description of the ghost attribute
- `type`: one of `bool`, `int` (unbounded), or `enum`
- `possible_values`: a list of possible values the ghost attribute can take (only for `enum`)

Notes:
- Include each object only once, do not include multiple variations of the same object.
- Include only the object type, not the variable name.
- If the object is a struct, use the pointer form (e.g. `struct foo *` not `struct foo`)
- If the object is an enum or a typedef, use the name of the enum or typedef (e.g. `myenum_t` not `enum myenum` or `uint8_t`)

[CODE]
{code}
[/CODE]
"""


class ObjectsResponse(BaseModel):
    objects: List[ObjectInfo]


def _scan_chunk(info: ProjectInfo, model: str, chunk: str) -> List[ObjectInfo]:
    llm = ChatOpenAI(model=model).with_structured_output(ObjectsResponse, strict=True)
    res: ObjectsResponse = llm.invoke(PROMPT_SCAN_CHUNK.format(code=chunk))
    return res.objects


def _validate_object(info: ProjectInfo, object: ObjectInfo) -> bool:
    """Ensure we can instantiate this object in code."""
    return validate_object(object.typ, info) is None


def find_objects(info: ProjectInfo, model: str, headers: List[str]) -> List[ObjectInfo]:
    def log(msg: str):
        print(f'[{Colors.BOLD}{Colors.GREEN}FIND{Colors.END}] {msg}')

    log('Splitting into chunks...')
    chunks = split_into_chunks(headers, 5000)
    log(f'Split into {len(chunks)} chunks')

    log('Scanning chunks...')
    objects = []
    for chunk in chunks:
        objects.extend(_scan_chunk(info, model, chunk))

    log(f'Found {len(objects)} objects')

    # Merge objects with the same type
    merged_objects = {}
    for obj in objects:
        if obj.typ not in merged_objects:
            merged_objects[obj.typ] = obj
        else:
            merged_objects[obj.typ].aliases.extend(obj.aliases)

    log(f'Merged {len(objects)} objects into {len(merged_objects)} unique objects')
    objects = list(merged_objects.values())

    log('Validating objects...')
    valid_objects = []
    for obj in objects:
        if _validate_object(info, obj):
            valid_objects.append(obj)
            log(f'Check {obj.typ}: {Colors.GREEN}VALID{Colors.END}')
        else:
            log(f'Check {obj.typ}: {Colors.RED}INVALID{Colors.END}')

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
    parser.add_argument('--model', type=str, default='gpt-4.1-nano', help='Model to use for scanning')
    parser.add_argument('--output', type=str, default=None, help='Path to the output file')
    parser.set_defaults(func=main)
