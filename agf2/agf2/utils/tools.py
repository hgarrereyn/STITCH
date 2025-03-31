from typing import List, Optional
from pathlib import Path
from ..rag.rag import RAG
from langchain_core.tools import tool
from . import repro



GLOBAL_BUILD_SCRIPT = None
def set_global_build_script(build_script: str):
    global GLOBAL_BUILD_SCRIPT
    GLOBAL_BUILD_SCRIPT = build_script


GLOBAL_RAG_OBJ = None
def load_global_rag(rag_path: str):
    global GLOBAL_RAG_OBJ
    GLOBAL_RAG_OBJ = RAG.load(Path(rag_path))


@tool
def test_program(code: str) -> str:
    """Compile and run a test case demonstrating the bug. Returns the output of running the program."""
    print(f'>> test_program:\n{code}')
    return repro.test_program(code, GLOBAL_BUILD_SCRIPT)


@tool
def search_codebase(query: str) -> List[str]:
    """Search the codebase for a given string. Returns matching lines with file and line number."""
    print(f'>> search_codebase: {query}')
    # Use the globally loaded RAG object instead of loading it each time
    global GLOBAL_RAG_OBJ
    
    # Perform the query
    results = GLOBAL_RAG_OBJ.query(query, top_n=8)
    
    # Format results
    output = []
    output.append(f"\nTop {len(results)} matches for query: {query}\n")
    
    for i, (chunk, similarity) in enumerate(results, 1):
        result_text = []
        result_text.append(f"Match {i} (similarity: {similarity:.3f}):")
        result_text.append(f"File: {chunk.filename}")
        result_text.append(f"Location: {chunk.start_point} - {chunk.end_point}")
        result_text.append(f"Content:")
        result_text.append("-" * 80)
        result_text.append(chunk.text.encode('utf-8', errors='ignore').decode('utf-8'))
        result_text.append("-" * 80)
        result_text.append("")
        output.append("\n".join(result_text))
        
    return output


@tool
def list_directory(path: str) -> List[str]:
    """List the contents of a directory in the codebase."""
    print(f'>> list_directory: {path}')
    try:
        p = Path(path)
        return [str(x.relative_to(path)).encode('utf-8', errors='ignore').decode('utf-8') for x in p.glob("*")]
    except Exception as e:
        return [f"Error listing directory: {str(e)}"]


@tool
def read_file(path: str, start_line: Optional[int] = None) -> str:
    """Read the contents of a file in the codebase."""
    print(f'>> read_file: {path} {start_line}')
    try:
        with open(path, 'r') as f:
            lines = f.readlines()
            if start_line is not None:
                lines = lines[start_line:]
            lines = lines[:100]
            return ("\n".join(lines)).encode('utf-8', errors='ignore').decode('utf-8')
    except Exception as e:
        return f"Error reading file: {str(e)}"
