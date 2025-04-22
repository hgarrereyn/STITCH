from typing import List, Optional
from pathlib import Path
import json

from langchain_core.tools import tool

from . import repro
from . import ctags
from .info import ProjectInfo


GLOBAL_PROJECT_INFO = None
def set_global_project_info(project_info: ProjectInfo):
    global GLOBAL_PROJECT_INFO
    GLOBAL_PROJECT_INFO = project_info


@tool
def test_program(code: str) -> str:
    """Compile and run a test case demonstrating the bug. Returns the output of running the program."""
    print(f'>> test_program:\n{code}')
    return repro.test_program(code, GLOBAL_PROJECT_INFO)


# @tool
# def search_codebase(query: str) -> List[str]:
#     """Search the codebase for a given string. Returns matching lines with file and line number."""
#     print(f'>> search_codebase: {query}')
#     # Use the globally loaded RAG object instead of loading it each time
#     global GLOBAL_RAG_OBJ
    
#     # Perform the query
#     results = GLOBAL_RAG_OBJ.query(query, top_n=8)
    
#     # Format results
#     output = []
#     output.append(f"\nTop {len(results)} matches for query: {query}\n")
    
#     for i, (chunk, similarity) in enumerate(results, 1):
#         result_text = []
#         result_text.append(f"Match {i} (similarity: {similarity:.3f}):")
#         result_text.append(f"File: {chunk.filename}")
#         result_text.append(f"Location: {chunk.start_point} - {chunk.end_point}")
#         result_text.append(f"Content:")
#         result_text.append("-" * 80)
#         result_text.append(chunk.text.encode('utf-8', errors='ignore').decode('utf-8'))
#         result_text.append("-" * 80)
#         result_text.append("")
#         output.append("\n".join(result_text))
        
#     return output


@tool
def search_ctags(identifier: str) -> str:
    """Search the codebase for a given identifier. Returns a list of matches with filename, line number, and identifier type, along with a preview of the file contents around the identifier."""
    print(f'>> search_ctags: {identifier}')
    tags = ctags.search_ctags(GLOBAL_PROJECT_INFO.codebase, identifier)
    
    results = {'matches': [tag.model_dump() for tag in tags]}
    return json.dumps(results)


@tool
def list_directory(path: str) -> str:
    """List the contents of a directory in the codebase."""
    print(f'>> list_directory: {path}')
    try:
        p = Path(path)
        return json.dumps({
            'files': [str(x.relative_to(path)).encode('utf-8', errors='ignore').decode('utf-8') for x in p.glob("*")],
        })
    except Exception as e:
        return json.dumps({
            'error': str(e),
        })


@tool
def read_file(path: str, start_line: Optional[int]) -> str:
    """Read 50 lines of a file in the codebase. Optionally, provide a starting line number to read from."""
    print(f'>> read_file: {path} {start_line}')
    try:
        with open(path, 'r') as f:
            lines = f.readlines()
            start_line = start_line or 1
            lines = lines[start_line - 1:]
            lines = lines[:50]
            content = (''.join(lines)).encode('utf-8', errors='ignore').decode('utf-8')
            return json.dumps({
                'content': content,
                'path': path,
                'start_line': start_line,
            })
    except Exception as e:
        return json.dumps({
            'error': str(e),
        })
