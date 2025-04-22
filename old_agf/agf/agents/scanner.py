from typing import List, Optional, Union, Annotated, Dict, Literal
from pydantic import BaseModel, Field
from operator import add
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END
from langgraph.types import Send
from ..utils.sqlitecache import SQLiteCacheFixed
from ..utils.validator import validate_object, validate_function
from langchain.globals import set_llm_cache
from ..utils.colors import Colors
from ..utils.info import ProjectInfo


class ScanChunkState(BaseModel):
    info: ProjectInfo
    model: str
    chunk: str
    find: Literal["objects", "functions"]
    replica: int

class Functions(BaseModel):
    functions: List[str]

class Object(BaseModel):
    typ: str
    description: str
    aliases: List[str]

class Objects(BaseModel):
    objects: List[Object]

class ScannerState(BaseModel):
    info: ProjectInfo
    model: str
    header_files: List[str]
    chunk_size: int = 5000

    # Generated
    chunks: List[str] = Field(default_factory=list)

    # Proposed
    proposed_objects: Annotated[List[Object], add] = Field(default_factory=list)
    proposed_functions: Annotated[List[str], add] = Field(default_factory=list)

    # Final
    objects: Annotated[List[Object], add] = Field(default_factory=list)
    functions: Annotated[List[str], add] = Field(default_factory=list)

class ValidateFunctionState(BaseModel):
    info: ProjectInfo
    function: str

class ValidateObjectState(BaseModel):
    info: ProjectInfo
    object: Object


PROMPT_OBJECTS = """
You are an expert C/C++ developer.

Examine the following chunk of C/C++ code and find all the custom object types defined in the code.

An object type is any custom type that exists beyond a single function call and stores context or data such as:
- identifiers (e.g. `myid_t`)
- struct pointers (e.g. `struct foo *`)
- class instances (e.g. `class::bar`)

Return a list of objects with the following fields:
- `typ`: the type of the object
- `description`: a short description of the object
- `aliases`: a list of aliases for the object (e.g. if the object is `struct foo *`, aliases might include `foo *`, `FOO_PTR`, `FOO_P`, etc...)

Notes:
- Include each object only once, do not include multiple variations of the same object.
- Include only the object type, not the variable name.
- If the object is a struct, use the pointer form (e.g. `struct foo *` not `struct foo`)
"""

PROMPT_FUNCTIONS = """
You are an expert C/C++ developer.

Examine the following chunk of C/C++ code and find all the public API functions defined in the code.

A public API function is any function that is part of the library's public interface, e.g.:
- functions declared in the header file
- methods of classes declared in the header file

Notes:
- Include only the function name, not the signature.
- Ensure functions are fully qualified (e.g. `foo::bar` not `bar`), with respect to namespaces and class methods.
"""


def split_file_into_chunks(content: str, chunk_size: int) -> List[str]:
    """Split a file into chunks of roughly equal size, trying to break at reasonable boundaries."""
    lines = content.split('\n')
    chunks = []
    current_chunk = []
    current_size = 0
    
    for line in lines:
        current_chunk.append(line)
        current_size += 1
        
        # When we hit chunk size, try to find a good break point
        if current_size >= chunk_size:
            # Look for a blank line or end of block/statement
            if line.strip() == '' or line.strip().endswith(';') or line.strip().endswith('}'):
                chunks.append('\n'.join(current_chunk))
                current_chunk = []
                current_size = 0
    
    # Add any remaining lines as the last chunk
    if current_chunk:
        chunks.append('\n'.join(current_chunk))
    
    return chunks

def initialize_state(state: ScannerState):
    """Initialize the scanner state by loading and splitting all files into chunks."""
    print(f"Initializing scanner state with {len(state.header_files)} files")

    # Process all files and split into chunks
    chunks = []
    for header_file in state.header_files:
        with open(header_file, 'r') as f:
            content = f.read()
        chunks.extend(split_file_into_chunks(content, state.chunk_size))

    print(f"Found {len(chunks)} chunks")
    
    return {
        "chunks": chunks,
    }

def dispatch_scans(state: ScannerState):
    """Dispatch scans to the workers."""
    replicas = state.info.tuning.scan_replicas
    workers = []
    for chunk in state.chunks:
        for replica in range(replicas):
            workers.append(Send("scan_chunk", ScanChunkState(info=state.info, model=state.model, chunk=chunk, find="objects", replica=replica)))
            workers.append(Send("scan_chunk", ScanChunkState(info=state.info, model=state.model, chunk=chunk, find="functions", replica=replica)))
    return workers

def scan_chunk(state: ScanChunkState):
    """Scan the chunks."""
    print(f"Scanning chunk for {state.find} (replica {state.replica})")
    
    llm = ChatOpenAI(model=state.model)
    if state.find == "objects":
        structured_llm = llm.with_structured_output(Objects, strict=True)
        res = structured_llm.invoke(PROMPT_OBJECTS + "\n\n[CODE]\n" + state.chunk + "\n[/CODE]")
        return {"proposed_objects": res.objects}
    elif state.find == "functions":
        structured_llm = llm.with_structured_output(Functions, strict=True)
        res = structured_llm.invoke(PROMPT_FUNCTIONS + "\n\n[CODE]\n" + state.chunk + "\n[/CODE]")
        return {"proposed_functions": res.functions}

def post_scan(state: ScannerState):
    pass

def dispatch_validations(state: ScannerState):
    """Validate the proposed objects and functions."""
    objects = []
    seen = set()
    for obj in state.proposed_objects:
        if obj.typ not in seen:
            objects.append(obj)
            seen.add(obj.typ)
    functions = list(set(state.proposed_functions))

    workers = []
    for object in objects:
        workers.append(Send("validate_object", ValidateObjectState(info=state.info, object=object)))
    for function in functions:
        workers.append(Send("validate_function", ValidateFunctionState(info=state.info, function=function)))
    return workers


def validate_object(state: ValidateObjectState):
    """Validate the proposed object."""
    print(f"[validate] {state.object.typ}")
    res = validate_object(state.object.typ, state.info)
    if res is None:
        print(f"[object] {state.object.typ} {Colors.GREEN}OK{Colors.END}", flush=True)
        return {"objects": [state.object]}
    else:
        print(f"[object] {state.object.typ} {Colors.RED}FAIL{Colors.END}", flush=True)
    return {"objects": [], "functions": []}

def validate_function(state: ValidateFunctionState):
    """Validate the proposed function."""
    print(f"[validate] {state.function}")
    res = validate_function(state.function, state.info)
    if res is None:
        print(f"[function] {state.function} {Colors.GREEN}OK{Colors.END}", flush=True)
        return {"functions": [state.function]}
    else:
        print(f"[function] {state.function} {Colors.RED}FAIL{Colors.END}", flush=True)
    return {"objects": [], "functions": []}


def build_scanner():
    """Build the scanner workflow graph."""
    workflow = StateGraph(ScannerState)
    
    # Add nodes
    workflow.add_node("initialize_state", initialize_state)

    # Scan chunks
    workflow.add_node("scan_chunk", scan_chunk)
    workflow.add_node("post_scan", post_scan)

    # Deduplicate
    workflow.add_node("validate_object", validate_object)
    workflow.add_node("validate_function", validate_function)
    
    # Connect the nodes
    workflow.set_entry_point("initialize_state")

    # After initialization, process chunks in parallel
    workflow.add_conditional_edges("initialize_state", dispatch_scans, {
        'scan_chunk': 'scan_chunk'
    })

    workflow.add_edge("scan_chunk", "post_scan")
    
    workflow.add_conditional_edges("post_scan", dispatch_validations, {
        'validate_object': 'validate_object',
        'validate_function': 'validate_function'
    })

    workflow.add_edge("validate_object", END)
    workflow.add_edge("validate_function", END)
    
    return workflow.compile()

def run_scanner(info: ProjectInfo, model: str, header_files: List[str], chunk_size: int = 5000):
    """Run the scanner on the given header files."""
    scanner = build_scanner()
    state = ScannerState(info=info, model=model, header_files=header_files, chunk_size=chunk_size)
    result = scanner.invoke(state)
    return result
