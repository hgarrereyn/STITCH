from argparse import ArgumentParser
from typing import Optional
from pydantic import BaseModel, Field
import tempfile
from pathlib import Path
import subprocess
import json
from openai import OpenAI
import os

from ..utils.colors import Colors
from ..data.graphinput import GraphInput
from .data import ProjectMetadata
from ..utils.crash_repro import observe_crash, build_and_observe_crash
from .pretty import pretty_print_code
from ..data.metadata import UsageTracker, UsageTokens


MAX_ITERATIONS = 5

PROMPT_DISTILL_AGENT = open(os.path.join(os.path.dirname(__file__), 'prompts/distill_agent.md')).read()


class CrashAssessment(BaseModel):
    title: str
    crash_type: str
    crash_type_reasoning: str
    minimized_testcase: str
    explanation: str
    can_patch_fuzzer: bool
    patch_guidance: str
    usage: Optional[UsageTracker] = Field(default_factory=UsageTracker)


class CompileError(BaseModel):
    note: str
    error: str


class ProcessResult(BaseModel):
    stdout: str
    stderr: str
    crash_report: Optional[str]


class TestCompileOutput(BaseModel):
    res: CompileError | ProcessResult


def pretty_print_compile_output(output: TestCompileOutput):
    if isinstance(output.res, CompileError):
        print(f'{Colors.RED}[-]{Colors.END} Compile error: {output.res.error}')
    else:
        print(f'{Colors.GREEN}[+]{Colors.END} Compile output:')
        pretty_print_code(output.res.stdout, language="cpp", title="stdout")
        pretty_print_code(output.res.stderr, language="cpp", title="stderr")
        if output.res.crash_report is not None:
            print(f'{Colors.GREEN}[+]{Colors.END} Crash report:')
            pretty_print_code(output.res.crash_report, language="json", title="crash report")


def tool_test_compile(testcase: str) -> TestCompileOutput:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        external_file_path = tmpdir / 'reproducer.cpp'
        with open(external_file_path, 'w') as f:
            f.write(testcase)

        res = subprocess.run(['agfi', 'build', 'external', str(tmpdir / 'reproducer')], check=True, capture_output=True)
        if res.returncode != 0:
            return TestCompileOutput(res=CompileError(note='Failed to compile testcase', error=res.stderr.decode()))
        
        # Run the binary and get stdin/stdout
        res = subprocess.run([str(tmpdir / 'reproducer')], capture_output=True)

        stdout = res.stdout.decode()
        stderr = res.stderr.decode()

        crash_report, _crash_summary = observe_crash([str((tmpdir / 'reproducer').absolute())])

        return TestCompileOutput(res=ProcessResult(stdout=stdout, stderr=stderr, crash_report=crash_report))


def run_distill_agent(harness: ProjectMetadata, testcase: str) -> CrashAssessment:
    client = OpenAI()

    usage = UsageTracker()

    # A bit hacky, find all the fuzzer stub markers in the testcase to get the fuzzer stub names
    # ===== START [<name>] =====
    fuzzer_stubs = set()
    for line in testcase.split('\n'):
        if '===== START [' in line:
            fuzzer_stubs.add(line.split('START [')[1].split(']')[0])
    
    fuzzer_stubs = list(fuzzer_stubs)

    p_objects = [o.model_dump() for o in harness.objects]
    p_functions = [f.model_dump() for f in harness.functions if f.name in fuzzer_stubs]
    p_endpoints = [e.model_dump() for e in harness.endpoints if e.name in fuzzer_stubs]

    print(f'{Colors.GREEN}[+]{Colors.END} Using {len(fuzzer_stubs)} fuzzer stubs')

    report, summary = build_and_observe_crash(str(testcase))
    print(f'{Colors.GREEN}[+]{Colors.END} Crash summary: {summary}')

    if report is None:
        print(f'{Colors.RED}[-]{Colors.END} No crash report found')
        return CrashAssessment(title=summary, crash_type='unknown', crash_type_reasoning=None, minimized_testcase=testcase, explanation=summary, usage=usage, can_patch_fuzzer=False, patch_guidance=None)

    ctx = [
        {
            "role": "developer",
            "content": PROMPT_DISTILL_AGENT
        },
        {
            "role": "user",
            "content": f'Objects: {json.dumps(p_objects)}\nFunctions: {json.dumps(p_functions)}\nFuzzer stubs: {json.dumps(p_endpoints)}'
        },
        {
            "role": "user",
            "content": f'Testcase: \n```\n{testcase}\n```\nCrash report: \n```\n{report}\n```'
        }
    ]

    tools = [
        {
            "type": "function",
            "name": "test_compile",
            "description": "Test a C++ testcase",
            "parameters": {
                "type": "object",
                "properties": {
                    "testcase": { "type": "string", "description": "The C++ testcase to test" }
                },
                "required": ["testcase"]
            }
        },
        {
            "type": "function",
            "name": "crash_assessment",
            "description": "Report a crash assessment",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": { "type": "string", "description": "A short single sentence title of what the crash is (e.g. '<thing> in <xyz> caused by <abc>')" },
                    "crash_type": { "type": "string", "description": "The type of the crash (`valid-crit`, `valid-high`, `valid-med`, `valid-impl`, `invalid-doc`, `invalid-fuzzer`, `unknown`)" },
                    "crash_type_reasoning": { "type": "string", "description": "A concise reasoning for why you made this assessment" },
                    "minimized_testcase": { "type": "string", "description": "A minimized C++ testcase that demonstrates the crash" },
                    "explanation": { "type": "string", "description": "A detailed explanation of your assessment" },
                    "can_patch_fuzzer": { "type": "boolean", "description": "Whether the fuzzer can be patched to fix or avoid the crash" },
                    "patch_guidance": { "type": "string", "description": "Detailed suggestions on how to patch the fuzzer to fix or avoid the crash (if `can_patch_fuzzer` is `true`)" }
                },
                "required": ["title", "crash_type", "crash_type_reasoning", "minimized_testcase", "explanation", "can_patch_fuzzer", "patch_guidance"]
            }
        }
    ]

    for i in range(MAX_ITERATIONS):
        print(f'{Colors.GREEN}[+]{Colors.END} Iteration {i+1} of {MAX_ITERATIONS}')
        response = client.responses.parse(
            model='gpt-5',
            input=ctx,
            tools=tools,
            reasoning={
                "effort": "low"
            }
        )
        usage.record_model('distill-agent', 'gpt-5', UsageTokens.from_openai(response.usage))

        ctx += response.output

        for message in response.output:
            match message.type:
                case 'reasoning':
                    if message.summary is not None and len(message.summary) > 0:
                        print(f'{Colors.PURPLE}[*]{Colors.END} Reasoning: {message.summary[0].text}')
                case 'function_call':
                    if message.name == 'test_compile':
                        args = json.loads(message.arguments)
                        testcase = args['testcase']

                        print(f'{Colors.GREEN}[+]{Colors.END} Testing testcase:')
                        pretty_print_code(testcase, language="cpp")

                        output = tool_test_compile(testcase)
                        print(f'{Colors.GREEN}[+]{Colors.END} Test compile output:')
                        pretty_print_compile_output(output)

                        ctx.append({
                            "type": "function_call_output",
                            "call_id": message.call_id,
                            "output": output.model_dump_json()
                        })
                    elif message.name == 'crash_assessment':
                        assessment = CrashAssessment.model_validate_json(message.arguments)
                        assessment.usage = usage
                        print(f'{Colors.GREEN}[+]{Colors.END} Crash assessment: ({assessment.crash_type}) {assessment.title}')
                        return assessment
                    else:
                        print(f'{Colors.RED}[-]{Colors.END} Unknown tool call: {message.name}')

    print(f'{Colors.RED}[-]{Colors.END} No crash assessment found')
    return CrashAssessment(title=summary, crash_type='unknown', crash_type_reasoning=None, minimized_testcase=testcase, explanation=summary, usage=usage, can_patch_fuzzer=False, patch_guidance=None)


def main(args):
    harness = ProjectMetadata.model_validate_json(open(args.harness).read())
    testcase = open(args.external_code).read()
    assessment = run_distill_agent(harness, testcase)
    print(f'{Colors.GREEN}[+]{Colors.END} Usage: {assessment.usage.cost_by_task()}')
    open(args.output, 'w').write(assessment.model_dump_json(indent=2))


def register(subparser: ArgumentParser):
    parser = subparser.add_parser('distill-agent')
    parser.add_argument('--harness', type=str, required=True)
    parser.add_argument('--external-code', type=str, required=True)
    parser.add_argument('--output', type=str, required=True)
    parser.set_defaults(func=main)
