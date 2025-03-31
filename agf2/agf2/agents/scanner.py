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


class ScannerState(BaseModel):
    info: ProjectInfo
    model: str
    header_files: List[str]  # List of header file paths to process
    chunk_size: int = 5000  # Number of lines per chunk
    
    # Generated
    chunks: List[str] = Field(default_factory=list)
    objects: Annotated[List[str], add] = Field(default_factory=list)
    functions: Annotated[List[str], add] = Field(default_factory=list)

    dedup_objects: List[str] = Field(default_factory=list)
    dedup_functions: List[str] = Field(default_factory=list)

class ChunkWorkerState(BaseModel):
    info: ProjectInfo
    model: str
    chunk: str

    messages: Annotated[list, add] = Field(default_factory=list)

    proposed_objects: List[str] = Field(default_factory=list)
    proposed_functions: List[str] = Field(default_factory=list)

    objects: List[str] = Field(default_factory=list)
    functions: List[str] = Field(default_factory=list)
    done: bool = False
    attempts: int = 5

class ChunkResults(BaseModel):
    objects: List[str]
    functions: List[str]

class DeduplicatedResults(BaseModel):
    objects: List[str]


PROMPT_CHUNK_OBJECTS_FUNCTIONS = '''
You are an expert C/C++ developer.

You will be given part of a C/C++ header file and asked to identify all object types and public API functions.

An object type is any library-defined type that exists beyond a single function call and stores context or data, e.g.:
- identifiers (e.g. `myid_t`)
- struct pointers (e.g. `struct foo *`)

A public API function is any function that is part of the library's public interface, e.g.:
- functions declared in the header file
- methods of classes declared in the header file

Tips:
- Include each object only once, do not include multiple variations of the same object.
- Include only the object type, not the variable name.
- Include only the function name, not the signature.

Note that this is a chunk of a larger file, so:
1. Some declarations may be incomplete or reference things defined in other chunks
2. Focus on complete object definitions that you can see
3. If an object definition is split across chunks, it's okay to miss it - it will be caught in another chunk

Return an object containing two lists:
- objects: a list of strings, where each string is the name of an object
- functions: a list of strings, where each string is the name of a public API function (not including the signature)
'''.strip()


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

def dispatch_worker_chunk(state: ScannerState):
    workers = []
    for chunk in state.chunks:
        ck = ChunkWorkerState(info=state.info, model=state.model, chunk=chunk)
        workers.append(Send("process_chunk", ck))
    return workers

def process_chunk(state: ChunkWorkerState) -> Dict:
    """Process a chunk."""
    chunk_worker = build_chunk_worker()
    res = chunk_worker.invoke(state)
    return {
        "objects": res["objects"],
        "functions": res["functions"]
    }

def process_chunk_init(state: ChunkWorkerState) -> Dict:
    """Initialize the chunk worker state."""
    return {
        "messages": [
            {"role": "system", "content": PROMPT_CHUNK_OBJECTS_FUNCTIONS},
            {"role": "user", "content": f"Find objects and functions in this code chunk:\n{state.chunk}"}
        ]
    }

def process_chunk_respond(state: ChunkWorkerState) -> Dict:
    """Respond to the chunk worker state."""
    llm = ChatOpenAI(model=state.model)
    structured_llm = llm.with_structured_output(ChunkResults)

    res = structured_llm.invoke(state.messages)
    return {
        "proposed_objects": res.objects,
        "proposed_functions": res.functions
    }

def process_chunk_validate(state: ChunkWorkerState) -> Dict:
    """Validate objects and functions."""
    objects = state.objects
    functions = state.functions

    errors = []

    for obj in state.proposed_objects:
        if obj in objects:
            continue
        print(f"Validating object {obj}", end=' ')
        res = validate_object(obj, state.info.build_script_ext, state.info.config)
        if res:
            print(f"{Colors.RED}FAIL{Colors.END}", flush=True)
            errors.append((obj, res))
        else:
            print(f"{Colors.GREEN}OK{Colors.END}", flush=True)
            objects.append(obj)

    for func in state.proposed_functions:
        if func in functions:
            continue
        print(f"Validating function {func}", end=' ')
        res = validate_function(func, state.info.build_script_ext, state.info.config)
        if res:
            print(f"{Colors.RED}FAIL{Colors.END}", flush=True)
            errors.append((func, res))
        else:
            print(f"{Colors.GREEN}OK{Colors.END}", flush=True)
            functions.append(func)

    if len(errors) > 0:
        return {
            "messages": [
                {"role": "user", "content": f"Some objects or functions are invalid. Please fix them and try again:\n{errors}"}
            ],
            "objects": objects,
            "functions": functions,
            "attempts": state.attempts - 1
        }
    else:
        return {
            "objects": objects,
            "functions": functions,
            "done": True,
            "attempts": 0
        }

def process_chunk_check(state: ChunkWorkerState) -> Literal['respond', 'done']:
    """Check if the chunk worker state is done."""
    if state.attempts == 0 or state.done:
        return 'done'
    else:
        return 'respond'

def deduplicate(state: ScannerState):
    """Deduplicate the objects and functions."""
    # Deduplicate functions (manually)
    dedup_objects = list(set(state.objects))
    dedup_functions = list(set(state.functions))

    print(f'Original objects: {len(state.objects)} -> Deduplicated objects: {len(dedup_objects)}')
    print(f'Original functions: {len(state.functions)} -> Deduplicated functions: {len(dedup_functions)}')

    return {
        "dedup_objects": dedup_objects,
        "dedup_functions": dedup_functions
    }

def build_chunk_worker():
    """Build the chunk worker workflow graph."""
    workflow = StateGraph(ChunkWorkerState)

    workflow.add_node("process_chunk_init", process_chunk_init)
    workflow.add_node("process_chunk_respond", process_chunk_respond)
    workflow.add_node("process_chunk_validate", process_chunk_validate)
    workflow.add_node("process_chunk_check", process_chunk_check)

    workflow.set_entry_point("process_chunk_init")
    workflow.add_edge("process_chunk_init", "process_chunk_respond")
    workflow.add_edge("process_chunk_respond", "process_chunk_validate")
    workflow.add_conditional_edges("process_chunk_validate", process_chunk_check, {
        'done': END,
        'respond': 'process_chunk_respond'
    })

    return workflow.compile()

def build_scanner():
    """Build the scanner workflow graph."""
    workflow = StateGraph(ScannerState)
    
    # Add nodes
    workflow.add_node("initialize", initialize_state)
    workflow.add_node("process_chunk", process_chunk)
    workflow.add_node("deduplicate", deduplicate)
    
    # Connect the nodes
    workflow.set_entry_point("initialize")

    # After initialization, process chunks in parallel
    workflow.add_conditional_edges("initialize", dispatch_worker_chunk, {
        'process_chunk': 'process_chunk'
    })

    workflow.add_edge("process_chunk", "deduplicate")
    workflow.add_edge("deduplicate", END)
    
    return workflow.compile()

def run_scanner(info: ProjectInfo, model: str, header_files: List[str], chunk_size: int = 5000):
    """Run the scanner on the given header files."""
    scanner = build_scanner()
    state = ScannerState(info=info, model=model, header_files=header_files, chunk_size=chunk_size)
    result = scanner.invoke(state)
    return result

def main(args):
    """Main entry point for the scanner agent."""
    info = ProjectInfo.model_validate_json(args.info)
    if not args.header_files:
        print("Error: No header files provided")
        return 1
    
    if args.cache:
        print(f"Using SQLite cache")
        cache = SQLiteCacheFixed(database_path=".cache.db")
        set_llm_cache(cache)
    else:
        print(f"Not using cache")

    result = run_scanner(info, args.model, args.header_files, args.chunk_size)
    print('Objects:', result['dedup_objects'])
    print('Functions:', result['dedup_functions'])

def register(subparsers):
    """Register the scanner command line interface."""
    parser = subparsers.add_parser('scanner', help='Scan header files for objects and functions')
    parser.add_argument('--info', type=str, required=True)
    parser.add_argument('--model', type=str, required=True)
    parser.add_argument('--chunk-size', type=int, default=5000, help='Number of lines per chunk')
    parser.add_argument('--cache', action='store_true', help='Use a SQLite cache to store results')
    parser.add_argument('header_files', nargs='+', help='Header files to scan')

    parser.set_defaults(func=main)
