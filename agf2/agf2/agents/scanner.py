from typing import List, Optional, Union, Annotated, Dict
from pydantic import BaseModel, Field
from operator import add
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END
from langgraph.types import Send
import re
from ..utils.sqlitecache import SQLiteCacheFixed
from langchain.globals import set_llm_cache


class ScannerState(BaseModel):
    header_files: List[str]  # List of header file paths to process
    chunk_size: int = 5000  # Number of lines per chunk
    
    # Generated
    chunks: List[str] = Field(default_factory=list)
    objects: Annotated[List[str], add] = Field(default_factory=list)
    functions: Annotated[List[str], add] = Field(default_factory=list)

    dedup_objects: List[str] = Field(default_factory=list)
    dedup_functions: List[str] = Field(default_factory=list)

class ChunkWorkerState(BaseModel):
    chunk: str

class ChunkResults(BaseModel):
    objects: List[str]
    functions: List[str]

class DeduplicatedResults(BaseModel):
    objects: List[str]


PROMPT_CHUNK_OBJECTS_FUNCTIONS = '''
You are an expert C++ developer. You will be given a chunk of a C++ header file and asked to identify all live objects that exist beyond a single function call and all public API functions.

For example, struct pointers, classes, etc... but not primitive types like int, float, etc.

Include each object only once, do not include multiple variations of the same object.
Do not return bare structs, only struct pointers.

Note that this is a chunk of a larger file, so:
1. Some declarations may be incomplete or reference things defined in other chunks
2. Focus on complete object definitions that you can see
3. If an object definition is split across chunks, it's okay to miss it - it will be caught in another chunk

Return an object containing two lists:
- objects: a list of strings, where each string is the name of an object
- functions: a list of strings, where each string is the name of a public API function
'''.strip()

PROMPT_DEDUPLICATE = '''
You are an expert C++ developer. You will be given a list of C++ objects that were identified from different chunks of the codebase.
Your task is to deduplicate these objects and return a list of unique objects.

Consider variations of the same object as duplicates (e.g. Foo* and Foo**) and return only the most canonical version.

Return a list of strings, where each string is a unique object name.
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
        workers.append(Send("process_chunk", ChunkWorkerState(chunk=chunk)))
    return workers

def process_chunk(state: ChunkWorkerState) -> Dict:
    """Process a single chunk to identify objects and functions."""
    llm = ChatOpenAI(model="gpt-4o-mini")
    structured_llm = llm.with_structured_output(ChunkResults)

    # Identify objects
    res = structured_llm.invoke([
        {"role": "system", "content": PROMPT_CHUNK_OBJECTS_FUNCTIONS},
        {"role": "user", "content": f"Find objects and functions in this code chunk:\n{state.chunk}"}
    ])

    print(f"Found {len(res.objects)} objects and {len(res.functions)} functions")

    return {
        "objects": res.objects,
        "functions": res.functions
    }

def deduplicate(state: ScannerState):
    """Deduplicate the objects and functions."""
    llm = ChatOpenAI(model="gpt-4o-mini")
    structured_llm = llm.with_structured_output(DeduplicatedResults)

    res = structured_llm.invoke([
        {"role": "system", "content": PROMPT_DEDUPLICATE},
        {"role": "user", "content": f"Deduplicate these objects:\n{state.objects}"}
    ])

    # Deduplicate functions (manually)
    dedup_functions = list(set(state.functions))

    print(f'Original objects: {len(state.objects)} -> Deduplicated objects: {len(res.objects)}')
    print(f'Original functions: {len(state.functions)} -> Deduplicated functions: {len(dedup_functions)}')

    return {
        "dedup_objects": res.objects,
        "dedup_functions": dedup_functions
    }

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

def run_scanner(header_files: List[str], chunk_size: int = 5000):
    """Run the scanner on the given header files."""
    scanner = build_scanner()
    state = ScannerState(header_files=header_files, chunk_size=chunk_size)
    result = scanner.invoke(state)
    return result

def main(args):
    """Main entry point for the scanner agent."""
    if not args.header_files:
        print("Error: No header files provided")
        return 1
    
    if args.cache:
        print(f"Using SQLite cache")
        cache = SQLiteCacheFixed(database_path=".cache.db")
        set_llm_cache(cache)
    else:
        print(f"Not using cache")

    result = run_scanner(args.header_files, args.chunk_size)
    print('Objects:', result['dedup_objects'])
    print('Functions:', result['dedup_functions'])

def register(subparsers):
    """Register the scanner command line interface."""
    parser = subparsers.add_parser('scanner', help='Scan header files for objects and functions')
    parser.add_argument('header_files', nargs='+', help='Header files to scan')
    parser.add_argument('--chunk-size', type=int, default=5000, help='Number of lines per chunk')

    parser.add_argument('--cache', action='store_true', help='Use a SQLite cache to store results')

    parser.set_defaults(func=main)
