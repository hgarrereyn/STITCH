from pathlib import Path
import shutil
import json
from typing import Optional, List, Any
import os
import tempfile
import subprocess
from pydantic import BaseModel

from ..data.project_info import ProjectInfo
from ..utils.colors import Colors
from ..utils.project import Project, ProjectConfig
from openai import OpenAI
from ..data.metadata import UsageTracker, UsageTokens
from .pretty import pretty_print_code


PROMPT_AGENT = open(Path(__file__).parent / 'prompt_agent.md').read()

MAX_CYCLES = 15


def _validate_path(root_path: str, relative_path: str) -> Optional[Path]:
    """
    Validate that the relative_path is within root_path to prevent directory traversal.
    Returns the resolved Path if valid, None if invalid.
    """
    root = Path(root_path).resolve()
    try:
        # Resolve the path to handle any .. sequences
        resolved_path = (root / relative_path).resolve()
        # Check if the resolved path is within the root directory
        if resolved_path.is_relative_to(root):
            return resolved_path
        else:
            return None
    except (OSError, ValueError):
        # Handle invalid paths or permission errors
        return None


class FileResult(BaseModel):
    content: str
    error: Optional[str]

def read_file(root_path: str, path: str, start_line: int, end_line: int) -> FileResult:
    validated_path = _validate_path(root_path, path)
    if validated_path is None:
        return FileResult(content='', error='Invalid path: directory traversal not allowed')
    if not validated_path.exists():
        return FileResult(content='', error='File path not found')
    with open(validated_path, 'r') as f:
        return FileResult(content='\n'.join(f.readlines()[start_line-1:end_line]), error=None)

class FileInfo(BaseModel):
    path: str
    lines: Optional[int]
    is_dir: bool

def list_dir(root_path: str, path: str) -> List[FileInfo]:
    validated_path = _validate_path(root_path, path)
    if validated_path is None:
        return []
    if not validated_path.exists() or not validated_path.is_dir():
        return []
    return [
        FileInfo(
            path=str(f.relative_to(validated_path)),
            lines=len(f.read_text(encoding='utf-8', errors='ignore').splitlines()) if f.is_file() else None,
            is_dir=f.is_dir()
        ) for f in validated_path.glob('*') if f.is_file() or f.is_dir()
    ]

class ConfigurationError(BaseModel):
    error_type: str
    error: Any

def test_configuration(config: ProjectConfig, dockerfile: str, test_program: str, info: ProjectInfo) -> Optional[ConfigurationError]:
    print(f'{Colors.GREEN}[+]{Colors.END} Testing configuration...')

    # Create a temporary directory
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        tmpdir.mkdir(parents=True, exist_ok=True)
        (tmpdir / 'agf.json').write_text(config.model_dump_json(indent=4))
        config_path = (tmpdir / 'config')
        config_path.mkdir(parents=True, exist_ok=True)
        (config_path / 'Dockerfile').write_text(dockerfile)
        (config_path / 'test.cpp').write_text(test_program)
        (config_path / 'info.json').write_text(info.model_dump_json(indent=4))

        project = Project(tmpdir)
        docker_err = project.build_docker_capture_errors(use_asan=True)
        if docker_err is not None:
            return ConfigurationError(error_type='Dockerfile did not build', error=docker_err.model_dump())

        mounts = [
            (str(config_path.absolute()), '/fuzz/workspace'),
        ]

        print(f'[{Colors.GREEN}+{Colors.END}] Testing test program...')
        try:
            res = project.invoke(
                mounts=mounts, 
                cmd='''
                    cd /fuzz/workspace &&
                    agfi build external test &&
                    ./test
                ''', stderr=subprocess.PIPE, stdout=subprocess.PIPE, text=True, timeout=20
            )
        except TimeoutError:
            return ConfigurationError(error_type='Test program timed out', error={
                'stdout': '',
                'stderr': 'Test program timed out after 20 seconds (if it reads stdio or expects external input, this is expected)',
            })
        
        if res.returncode != 0:
            return ConfigurationError(error_type='Test program did not compile or run correctly', error={
                'stdout': res.stdout,
                'stderr': res.stderr,
            })

        print(f'[{Colors.GREEN}+{Colors.END}] Test program test passed')
        print(f'[{Colors.GREEN}+{Colors.END}] Test program output:')
        pretty_print_code(res.stdout, language="markdown", title="stdout")

        print(f'[{Colors.GREEN}+{Colors.END}] Testing codegen configuration...')
        res = project.invoke(
            mounts=mounts, 
            cmd='''
                cd /fuzz/workspace &&
                agfi build test-setup
            ''', stderr=subprocess.PIPE, stdout=subprocess.PIPE, text=True
        )

        if res.returncode != 0:
            return ConfigurationError(error_type='Codegen configuration did not compile', error={
                'stdout': res.stdout,
                'stderr': res.stderr,
            })

        print(f'[{Colors.GREEN}+{Colors.END}] Codegen configuration test passed')

        return None
        

def find_readme(project_src: str) -> Optional[str]:
    if (Path(project_src) / 'README.md').exists():
        return (Path(project_src) / 'README.md').read_text()
    elif (Path(project_src) / 'README.txt').exists():
        return (Path(project_src) / 'README.txt').read_text()
    elif (Path(project_src) / 'README').exists():
        return (Path(project_src) / 'README').read_text()
    return None


class AutogenResult(BaseModel):
    dockerfile: str
    test_program: str
    info: ProjectInfo


def run_agent(config: ProjectConfig, code_src: Path, usage: UsageTracker) -> Optional[AutogenResult]:
    client = OpenAI()

    # Find a README.md if it exists
    readme = find_readme(str(code_src))
    tools = [
        {
            "type": "function",
            "name": "read_file",
            "description": "Read a file and return the content as a string",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": { "type": "string", "description": "The path to the file to read" },
                    "start_line": { "type": "integer", "description": "The line number to start reading from (one-indexed)" },
                    "end_line": { "type": "integer", "description": "The line number to end reading at (one-indexed)" }
                },
                "required": ["path", "start_line", "end_line"]
            }
        },
        {
            "type": "function",
            "name": "list_dir",
            "description": "List the files in a directory and return the names and number of lines in each file",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": { "type": "string", "description": "The path to the directory to list" }
                },
                "required": ["path"]
            }
        },
        {
            "type": "function",
            "name": "test_configuration",
            "description": "Test the configuration by building the project and running the test program",
            "parameters": {
                "type": "object",
                "properties": {
                    "dockerfile": { "type": "string", "description": "The Dockerfile to test" },
                    "test_program": { "type": "string", "description": "The test program to test" },
                    "info": {
                        "type": "object",
                        "properties": {
                            "build_flags": { "type": "array", "items": { "type": "string" } },
                            "codegen": {
                                "type": "object",
                                "properties": {
                                    "headers": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "path": { "type": "string" },
                                                "language": { "type": "string" }
                                            },
                                            "required": ["path", "language"]
                                        }
                                    },
                                    "defines": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "name": { "type": "string" }, 
                                                "value": { "type": "string" }
                                            },
                                            "required": ["name", "value"]
                                        }
                                    }
                                },
                                "required": ["headers", "defines"]
                            }
                        },
                        "required": ["build_flags", "codegen"]
                    }
                },
                "required": ["dockerfile", "test_program", "info"]
            }
        }
    ]

    ctx = [
        {
            'role': 'developer',
            'content': PROMPT_AGENT
        },
        {
            'role': 'user',
            'content': f'Project: {config.name}\nRepo: {config.repo}' + (f'\nReadme: {readme}' if readme is not None else '')
        }
    ]

    final_configuration = None
    for _ in range(MAX_CYCLES):
        response = client.responses.parse(
            model='gpt-5',
            input=ctx,
            tools=tools,
            reasoning={
                "effort": "low",
                "summary": "auto"
            },
            tool_choice='required',

        )
        ctx += response.output
        usage.record_model('gen-agent', 'gpt-5', UsageTokens.from_openai(response.usage))

        for message in response.output:
            match message.type:
                case 'reasoning':
                    if message.summary is not None and len(message.summary) > 0:
                        print(f'{Colors.PURPLE}[*]{Colors.END} Reasoning: {message.summary[0].text}')
                case 'function_call':
                    match message.name:
                        case 'read_file':
                            args = json.loads(message.arguments)
                            print(f'{Colors.GREEN}[+]{Colors.END} Reading file: {args["path"]} from line {args["start_line"]} to line {args["end_line"]}')
                            path = args['path']
                            start_line = args['start_line']
                            end_line = args['end_line']
                            content = read_file(str(code_src), path, start_line, end_line)
                            output = json.dumps(content.model_dump())
                            ctx.append({
                                "type": "function_call_output",
                                "call_id": message.call_id,
                                "output": output
                            })
                        case 'list_dir':
                            args = json.loads(message.arguments)
                            path = args['path']
                            files = list_dir(str(code_src), path)
                            print(f'{Colors.GREEN}[+]{Colors.END} Listing directory: {args["path"]}:')
                            for file in sorted(files, key=lambda x: x.path):
                                print(f'{Colors.CYAN}  -{Colors.END} {file.path} ({file.lines} lines)' if file.lines is not None else f'{Colors.CYAN}  -{Colors.END} {file.path} (directory)')
                            output = json.dumps([x.model_dump() for x in files])
                            ctx.append({
                                "type": "function_call_output",
                                "call_id": message.call_id,
                                "output": output
                            })
                        case 'test_configuration':
                            args = json.loads(message.arguments)
                            print(f'{Colors.GREEN}[+]{Colors.END} Testing configuration...')
                            dockerfile = args['dockerfile']
                            test_program = args['test_program']
                            info = ProjectInfo.model_validate(args['info'])

                            pretty_print_code(dockerfile, language="dockerfile", title="Dockerfile")
                            pretty_print_code(test_program, language="cpp", title="Test Program")
                            pretty_print_code(info.model_dump_json(indent=2), language="json", title="Configuration")

                            output = test_configuration(config, dockerfile, test_program, info)
                            if output is None:
                                print(f'{Colors.GREEN}[+]{Colors.END} Configuration test passed')
                                final_configuration = AutogenResult(
                                    dockerfile=dockerfile,
                                    test_program=test_program,
                                    info=info
                                )
                                break
                            else:
                                print(f'{Colors.RED}[-]{Colors.END} Configuration test failed')
                                print(f'{Colors.RED}[-]{Colors.END} Error:')
                                pretty_print_code(output.model_dump_json(indent=2), language="json", title="Error")

                            ctx.append({
                                "type": "function_call_output",
                                "call_id": message.call_id,
                                "output": output.model_dump_json()
                            })
                        case _:
                            print(f'{Colors.RED}[-]{Colors.END} Unknown function call: {message.name}')

        if final_configuration is not None:
            break

    if final_configuration is not None:
        return final_configuration
    
    print(f'{Colors.RED}[-]{Colors.END} Max cycles reached')
    return None
