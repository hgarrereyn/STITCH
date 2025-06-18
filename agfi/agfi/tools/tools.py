from typing import List, Optional, Annotated
from pathlib import Path
import json

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from ..data.project_info import ProjectInfo
from .ctags import search_ctags as search_ctags_tool
from ..utils.colors import Colors


def tool_log(msg: str):
    print(f'{Colors.BOLD}{Colors.YELLOW}>>{Colors.END} {msg}')


@tool(parse_docstring=True)
def search_ctags(info: Annotated[ProjectInfo, InjectedState('info')], identifier: str):
    """Search the codebase for a given identifier with ctags.
    
    Args:
        info: ProjectInfo object containing codebase information.
        identifier: String identifier to search for.
    """
    tool_log(f'Searching for {identifier}...')
    tags = search_ctags_tool(info.codebase, identifier)
    
    results = {'matches': [tag.model_dump() for tag in tags]}
    return json.dumps(results)


@tool(parse_docstring=True)
def list_directory(info: Annotated[ProjectInfo, InjectedState('info')], path: str):
    """List the contents of a directory in the codebase.
    
    Args:
        info: ProjectInfo object containing codebase information.
        path: Path to directory to list contents of.
    """
    tool_log(f'Listing directory {path}...')
    try:
        p = Path(path)
        return json.dumps({
            'files': [str(x.relative_to(path)).encode('utf-8', errors='ignore').decode('utf-8') for x in p.glob("*")],
        })
    except Exception as e:
        return json.dumps({
            'error': str(e),
        })


@tool(parse_docstring=True)
def read_file(info: Annotated[ProjectInfo, InjectedState('info')], path: str, start_line: Optional[int]):
    """Read 50 lines of a file in the codebase. Optionally, provide a starting line number to read from.
    
    Args:
        info: ProjectInfo object containing codebase information.
        path: Path to file to read.
        start_line: Optional starting line number to read from.
    """
    tool_log(f'Reading file {path} from line {start_line}...')
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
