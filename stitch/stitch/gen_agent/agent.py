from pathlib import Path
import shutil
import json
from typing import Optional, List
import tempfile
import subprocess
from pydantic import BaseModel

from ..data.project_info import ProjectInfo
from .._project import Project, ProjectConfig
from openai import OpenAI
from ..data.metadata import UsageTracker, UsageTokens
from . import pretty as ui


PROMPT_AGENT = open(Path(__file__).parent / "prompt_agent.md").read()

MAX_CYCLES = 30
MAX_ERROR_SIZE = 8000


def _validate_path(root_path: str, relative_path: str) -> Optional[Path]:
    root = Path(root_path).resolve()
    try:
        resolved_path = (root / relative_path).resolve()
        if resolved_path.is_relative_to(root):
            return resolved_path
        return None
    except (OSError, ValueError):
        return None


class FileResult(BaseModel):
    path: str
    content: str
    error: Optional[str]


def read_file(root_path: str, path: str, start_line: int, end_line: int) -> FileResult:
    validated_path = _validate_path(root_path, path)
    if validated_path is None:
        return FileResult(path=path, content="", error="Invalid path: directory traversal not allowed")
    if not validated_path.exists():
        return FileResult(path=path, content="", error="File path not found")
    if not validated_path.is_file():
        return FileResult(path=path, content="", error="Path is not a file")
    with open(validated_path, "rb") as f:
        content = f.read().decode("utf-8", errors="ignore")
        return FileResult(path=path, content="\n".join(content.splitlines()[start_line - 1 : end_line]), error=None)


class FileInfo(BaseModel):
    name: str
    lines: Optional[int]
    is_dir: bool


class DirResult(BaseModel):
    path: str
    files: List[FileInfo]
    error: Optional[str]


def list_dir(root_path: str, path: str) -> DirResult:
    validated_path = _validate_path(root_path, path)
    if validated_path is None:
        return DirResult(path=path, files=[], error="Invalid path: directory traversal not allowed")
    if not validated_path.exists() or not validated_path.is_dir():
        return DirResult(path=path, files=[], error="Path is not a directory")
    files = [
        FileInfo(
            name=str(f.relative_to(validated_path)),
            lines=len(f.read_text(encoding="utf-8", errors="ignore").splitlines()) if f.is_file() else None,
            is_dir=f.is_dir(),
        )
        for f in validated_path.glob("*")
        if f.is_file() or f.is_dir()
    ]
    files.sort(key=lambda x: x.name)
    return DirResult(path=path, files=files, error=None)


class ConfigurationError(BaseModel):
    error_message: str
    stdout: str
    stderr: str


class TestResult(BaseModel):
    success: bool
    message: str
    error: Optional[ConfigurationError] = None


class ConfigurationResult(BaseModel):
    dockerfile_result: TestResult
    test_program_result: TestResult
    codegen_result: TestResult


def test_configuration(config: ProjectConfig, dockerfile: str, test_program: str, info: ProjectInfo) -> ConfigurationResult:
    ui.step_header("Testing configuration")
    result = ConfigurationResult(
        dockerfile_result=TestResult(success=False, message="(not tested)"),
        test_program_result=TestResult(success=False, message="(not tested)"),
        codegen_result=TestResult(success=False, message="(not tested)"),
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        tmpdir.mkdir(parents=True, exist_ok=True)
        (tmpdir / "stitch.json").write_text(config.model_dump_json(indent=4))
        config_path = tmpdir / "config"
        config_path.mkdir(parents=True, exist_ok=True)
        (config_path / "Dockerfile").write_text(dockerfile)
        (config_path / "test.cpp").write_text(test_program)
        (config_path / "info.json").write_text(info.model_dump_json(indent=4))

        project = Project(tmpdir)
        docker_err, image_tag = project.build_docker_capture_errors(use_asan=True)
        if docker_err is not None:
            stdout = docker_err.stdout[:MAX_ERROR_SIZE] + "\n… (truncated)" if len(docker_err.stdout) > MAX_ERROR_SIZE else docker_err.stdout
            stderr = docker_err.stderr[:MAX_ERROR_SIZE] + "\n… (truncated)" if len(docker_err.stderr) > MAX_ERROR_SIZE else docker_err.stderr
            result.dockerfile_result = TestResult(success=False, message="Dockerfile did not build", error=ConfigurationError(error_message="Dockerfile did not build", stdout=stdout, stderr=stderr))
            return result
        result.dockerfile_result = TestResult(success=True, message="Dockerfile built successfully")

        mounts = [(str(config_path.absolute()), "/fuzz/workspace")]

        ui.action("Running test program")
        try:
            res = project.invoke(
                mounts=mounts,
                image=image_tag,
                cmd="cd /fuzz/workspace && stitchi build external test && ./test",
                stderr=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
                timeout=120,
            )
        except TimeoutError:
            result.test_program_result = TestResult(success=False, message="Test program timed out")
            return result

        if res.returncode != 0:
            stdout = res.stdout[:MAX_ERROR_SIZE] + "\n… (truncated)" if len(res.stdout) > MAX_ERROR_SIZE else res.stdout
            stderr = res.stderr[:MAX_ERROR_SIZE] + "\n… (truncated)" if len(res.stderr) > MAX_ERROR_SIZE else res.stderr
            result.test_program_result = TestResult(success=False, message="Test program did not compile or run correctly", error=ConfigurationError(error_message="Test program did not compile or run correctly", stdout=stdout, stderr=stderr))
            return result
        result.test_program_result = TestResult(success=True, message="Test program compiled and ran correctly")

        ui.success("Test program passed")
        if res.stdout.strip():
            ui.code_panel(res.stdout, language="text", title="stdout", border="dim")

        ui.action("Running codegen check")
        res = project.invoke(
            mounts=mounts,
            image=image_tag,
            cmd="cd /fuzz/workspace && stitchi validate setup",
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )

        if res.returncode != 0:
            stdout = res.stdout[:MAX_ERROR_SIZE] + "\n… (truncated)" if len(res.stdout) > MAX_ERROR_SIZE else res.stdout
            stderr = res.stderr[:MAX_ERROR_SIZE] + "\n… (truncated)" if len(res.stderr) > MAX_ERROR_SIZE else res.stderr
            result.codegen_result = TestResult(success=False, message="External codegen failed", error=ConfigurationError(error_message="External codegen failed", stdout=stdout, stderr=stderr))
            return result
        result.codegen_result = TestResult(success=True, message="External codegen succeeded")

        ui.success("Codegen check passed")
        return result


def find_readme(project_src: str) -> Optional[str]:
    for name in ("README.md", "README.txt", "README"):
        p = Path(project_src) / name
        if p.exists():
            return p.read_text()
    return None


class AutogenResult(BaseModel):
    dockerfile: str
    test_program: str
    info: ProjectInfo


def run_agent(config: ProjectConfig, code_src: Path, usage: UsageTracker, extra: Optional[str], model: str = "gpt-5") -> Optional[AutogenResult]:
    client = OpenAI()

    readme = find_readme(str(code_src))
    tools = [
        {
            "type": "function",
            "name": "read_file",
            "description": "Read a file and return the content as a string",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "The path to the file to read"},
                    "start_line": {"type": "integer", "description": "The line number to start reading from (one-indexed)"},
                    "end_line": {"type": "integer", "description": "The line number to end reading at (one-indexed)"},
                },
                "required": ["path", "start_line", "end_line"],
            },
        },
        {
            "type": "function",
            "name": "list_dir",
            "description": "List the files in a directory and return the names and number of lines in each file",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "The path to the directory to list"}},
                "required": ["path"],
            },
        },
        {
            "type": "function",
            "name": "test_configuration",
            "description": "Test the configuration by building the project and running the test program",
            "parameters": {
                "type": "object",
                "properties": {
                    "dockerfile": {"type": "string"},
                    "test_program": {"type": "string"},
                    "info": {
                        "type": "object",
                        "properties": {
                            "build_flags": {"type": "array", "items": {"type": "string"}},
                            "codegen": {
                                "type": "object",
                                "properties": {
                                    "headers": {"type": "array", "items": {"type": "object", "properties": {"path": {"type": "string"}, "language": {"type": "string"}}, "required": ["path", "language"]}},
                                    "defines": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}, "value": {"type": "string"}}, "required": ["name", "value"]}},
                                },
                                "required": ["headers", "defines"],
                            },
                        },
                        "required": ["build_flags", "codegen"],
                    },
                },
                "required": ["dockerfile", "test_program", "info"],
            },
        },
    ]

    ctx = [
        {"role": "developer", "content": PROMPT_AGENT},
        {"role": "user", "content": f"Project: {config.name}\nRepo: {config.repo}" + (f"\nReadme: {readme}" if readme else "")},
    ]
    if extra is not None:
        ctx.append({"role": "user", "content": "# User provided extra context\n" + extra})

    ui.phase("Agent", "exploring project and generating configuration")

    final_configuration = None
    cycle = 0
    for _ in range(MAX_CYCLES):
        cycle += 1
        response = client.responses.parse(
            model=model,
            input=ctx,
            tools=tools,
            reasoning={"effort": "low", "summary": "auto"},
            tool_choice="required",
        )
        ctx += response.output
        usage.record_model("gen-agent", model, UsageTokens.from_openai(response.usage))

        for message in response.output:
            match message.type:
                case "reasoning":
                    if message.summary is not None and len(message.summary) > 0:
                        ui.reasoning(message.summary[0].text)
                case "function_call":
                    match message.name:
                        case "read_file":
                            args = json.loads(message.arguments)
                            ui.action(f"read [bold]{args['path']}[/bold]  [dim]{args['start_line']}–{args['end_line']}[/dim]")
                            content = read_file(str(code_src), args["path"], args["start_line"], args["end_line"])
                            if content.error is not None:
                                ui.error(content.error)
                            else:
                                ui.file_entry(content.path, f"{len(content.content.splitlines())} lines")
                            ctx.append({"type": "function_call_output", "call_id": message.call_id, "output": json.dumps(content.model_dump())})
                        case "list_dir":
                            args = json.loads(message.arguments)
                            ui.action(f"list [bold]{args['path']}[/bold]")
                            dir_result = list_dir(str(code_src), args["path"])
                            if dir_result.error is not None:
                                ui.error(dir_result.error)
                            else:
                                for file in dir_result.files:
                                    detail = f"{file.lines} lines" if file.lines is not None else "dir"
                                    ui.file_entry(file.name, detail)
                            ctx.append({"type": "function_call_output", "call_id": message.call_id, "output": json.dumps(dir_result.model_dump())})
                        case "test_configuration":
                            args = json.loads(message.arguments)
                            ui.phase("Test", f"validating configuration (cycle {cycle})")
                            dockerfile = args["dockerfile"]
                            test_program = args["test_program"]
                            info = ProjectInfo.model_validate(args["info"])

                            ui.code_panel(dockerfile, language="dockerfile", title="Dockerfile")
                            ui.code_panel(test_program, language="cpp", title="Test Program")
                            ui.code_panel(info.model_dump_json(indent=2), language="json", title="Configuration")

                            config_result = test_configuration(config, dockerfile, test_program, info)

                            if config_result.dockerfile_result.success and config_result.test_program_result.success and config_result.codegen_result.success:
                                ui.success("All configuration tests passed")
                                final_configuration = AutogenResult(dockerfile=dockerfile, test_program=test_program, info=info)
                                break

                            for label, tr in [("Dockerfile", config_result.dockerfile_result), ("Test program", config_result.test_program_result), ("Codegen", config_result.codegen_result)]:
                                if tr.success:
                                    ui.success(f"{label} passed")
                                else:
                                    ui.error(f"{label} failed")
                                    if tr.error is not None:
                                        ui.error_panel(label, tr.error.error_message, tr.error.stdout, tr.error.stderr)

                            ctx.append({"type": "function_call_output", "call_id": message.call_id, "output": config_result.model_dump_json()})
                        case _:
                            ui.error(f"Unknown function call: {message.name}")

        if final_configuration is not None:
            break

    if final_configuration is not None:
        return final_configuration

    ui.error("Max cycles reached without a valid configuration")
    return None
