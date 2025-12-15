from argparse import ArgumentParser
from typing import Optional, List, Literal
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


MAX_ITERATIONS = 10

PROMPT_DISTILL_AGENT = open(os.path.join(os.path.dirname(__file__), 'prompts/distill_agent.md')).read()
PROMPT_DISTILL_AGENT_LAST = open(os.path.join(os.path.dirname(__file__), 'prompts/distill_agent_last.md')).read()


class CompileError(BaseModel):
    note: str
    error: str


class ProcessResult(BaseModel):
    stdout: str
    stderr: str
    crash_report: Optional[str]


class TestCompileOutput(BaseModel):
    res: CompileError | ProcessResult


class CrashAssessmentTest(BaseModel):
    testcase: str
    result: TestCompileOutput


# --- Structured triage (replaces crash_type) ---

ApiUsage = Literal["intended", "questionable", "misuse", "harness_bug", "unknown"]
PreconditionDocumented = Literal["yes", "no", "unclear", "unknown"]
TriggerSurface = Literal[
    "untrusted_input",
    "file_parse",
    "network_parse",
    "config",
    "env",
    "api_sequence_only",
    "unknown",
]
Reproducibility = Literal["always", "often", "flaky", "unknown"]
AttackerControl = Literal["none", "partial", "full", "unknown"]
CrashMechanism = Literal[
    "oob_write",
    "uaf",
    "double_free",
    "heap_overflow",
    "stack_overflow",
    "oob_read",
    "null_deref",
    "uninit_read",
    "assert_abort",
    "abort",
    "logic_error",
    "resource_exhaustion",
    "unknown",
]
MemorySafety = Literal["yes", "no", "unknown"]
ExploitLikelihood = Literal["likely", "possible", "unlikely", "no", "unknown"]
SecurityImpact = Literal["rce", "arbitrary_write", "info_leak", "dos", "none", "unknown"]
ReportRecommendation = Literal["security_private", "public_bug", "dont_report", "needs_more_info"]
ReportPriority = Literal["p0_potential_rce", "p1_security", "p2_stability", "p3_dev_misuse"]


class CrashTriage(BaseModel):
    # Legitimacy / intended use
    api_usage: ApiUsage = "unknown"
    misuse_category: List[
        Literal["precondition", "lifetime", "ownership", "nullability", "bounds", "threading", "state_machine", "other"]
    ] = Field(default_factory=list)
    precondition_documented: PreconditionDocumented = "unknown"
    why_not_intended: Optional[str] = None

    # Triggerability / attacker control
    trigger_surface: TriggerSurface = "unknown"
    requires_crafted_data: bool = False
    requires_unusual_sequence: bool = False
    reproducibility: Reproducibility = "unknown"
    attacker_control: AttackerControl = "unknown"

    # Crash mechanism / bug class
    crash_mechanism: CrashMechanism = "unknown"
    memory_safety: MemorySafety = "unknown"
    debug_only: bool = False

    # Exploitability / security impact
    exploitation_likelihood: ExploitLikelihood = "unknown"
    security_impact: SecurityImpact = "unknown"
    why_exploitable_or_not: Optional[str] = None
    confidence: float = 0.5

    # Reporting guidance (maintainer-facing)
    report_recommendation: ReportRecommendation = "needs_more_info"
    report_priority: ReportPriority = "p2_stability"
    report_rationale: str

    # Scoring (two tracks)
    priority_score_llm: int = Field(ge=0, le=100)
    priority_score_computed: Optional[int] = Field(default=None, ge=0, le=100)


def compute_priority_score(triage: CrashTriage) -> int:
    """
    Deterministic priority score (0-100) computed from atomic fields.
    This is intentionally conservative: only strong, plausible exploit primitives score near the top.
    """
    score = 0

    # Penalize obvious non-bugs / fuzzer bugs
    if triage.api_usage == "harness_bug":
        return 0
    if triage.api_usage == "misuse":
        score -= 20
    elif triage.api_usage == "questionable":
        score -= 5
    elif triage.api_usage == "intended":
        score += 10

    # Primitive strength: writes > frees > reads > clean crashes
    mech = triage.crash_mechanism
    if mech in ("oob_write", "heap_overflow", "stack_overflow"):
        score += 60
    elif mech in ("uaf", "double_free"):
        score += 55
    elif mech in ("oob_read", "uninit_read"):
        score += 25
    elif mech in ("null_deref",):
        score += 10
    elif mech in ("assert_abort", "abort"):
        score += 2
    elif mech in ("resource_exhaustion",):
        score += 8

    if triage.memory_safety == "yes":
        score += 10

    # Attacker control + surface
    if triage.trigger_surface in ("file_parse", "network_parse", "untrusted_input"):
        score += 15
    elif triage.trigger_surface == "api_sequence_only":
        score -= 5

    if triage.attacker_control == "full":
        score += 10
    elif triage.attacker_control == "partial":
        score += 5
    elif triage.attacker_control == "none":
        score -= 5

    if triage.requires_crafted_data:
        score += 5  # crafted-input-triggered bugs are often more security-relevant
    if triage.requires_unusual_sequence:
        score -= 5  # weird sequences often indicate misuse / low reachability

    # Exploitability judgment (still deterministic, but uses model’s chosen bucket)
    if triage.exploitation_likelihood == "likely":
        score += 15
    elif triage.exploitation_likelihood == "possible":
        score += 8
    elif triage.exploitation_likelihood == "unlikely":
        score -= 3
    elif triage.exploitation_likelihood == "no":
        score -= 10

    if triage.security_impact in ("rce", "arbitrary_write"):
        score += 10
    elif triage.security_impact == "info_leak":
        score += 5
    elif triage.security_impact == "dos":
        score += 2
    elif triage.security_impact == "none":
        score -= 5

    if triage.debug_only:
        score -= 10

    # Clamp
    score = max(0, min(100, score))
    return score


# TODO: remove
class OldCrashAssessment(BaseModel):
    title: str
    crash_type: str
    crash_type_reasoning: str
    minimized_testcase: str
    explanation: str
    can_patch_fuzzer: bool
    patch_guidance: str
    usage: Optional[UsageTracker] = Field(default_factory=UsageTracker)


class CrashAssessment(BaseModel):
    title: str
    triage: CrashTriage
    minimized_testcase: str
    explanation: str
    can_patch_fuzzer: bool
    patch_guidance: str
    relevant_functions: List[str]

    # Added later
    duplicate_of: Optional[str] = None
    duplicate_reason: Optional[str] = None
    usage: Optional[UsageTracker] = Field(default_factory=UsageTracker)
    test_iterations: List[CrashAssessmentTest] = Field(default_factory=list)


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

        res = subprocess.run(['agfi', 'build', 'external', str(tmpdir / 'reproducer')], capture_output=True)
        if res.returncode != 0:
            return TestCompileOutput(res=CompileError(note='Failed to compile testcase', error=res.stderr.decode()))
        
        # Run the binary and get stdin/stdout
        try:
            res = subprocess.run([str(tmpdir / 'reproducer')], capture_output=True, timeout=60)
        except subprocess.TimeoutExpired:
            return TestCompileOutput(res=CompileError(note='Testcase timed out', error='Testcase timed out'))

        stdout = res.stdout.decode('utf-8', errors='ignore')
        stderr = res.stderr.decode('utf-8', errors='ignore')

        crash_report, _crash_summary = observe_crash([str((tmpdir / 'reproducer').absolute())])

        return TestCompileOutput(res=ProcessResult(stdout=stdout, stderr=stderr, crash_report=crash_report))


def run_distill_agent(harness: ProjectMetadata, testcase: str, history: Optional[list[dict]] = None) -> CrashAssessment:
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
        return CrashAssessment(
            title=summary,
            triage=CrashTriage(
                report_rationale="No crash report was produced by the runtime observer; insufficient information to assess legitimacy/exploitability.",
                priority_score_llm=0,
                priority_score_computed=0,
            ),
            minimized_testcase=testcase,
            explanation='',
            usage=usage,
            can_patch_fuzzer=False,
            patch_guidance=None,
            relevant_functions=fuzzer_stubs,
            test_iterations=[]
        )

    ctx = [
        {
            "role": "developer",
            "content": PROMPT_DISTILL_AGENT
        },
        {
            "role": "user",
            "content": f'Objects: {json.dumps(p_objects)}\nFunctions: {json.dumps(p_functions)}\nFuzzer stubs: {json.dumps(p_endpoints)}'
        },
    ]

    # Optionally provide a compact history of previous crashes so the agent can
    # recognize when the current crash is a duplicate of a prior one.
    if history:
        # Keep representation compact: one JSON object per line.
        history_lines = "\n".join(json.dumps(item) for item in history)
        ctx.append(
            {
                "role": "user",
                "content": (
                    "Previous crashes (one JSON object per line, with fields like "
                    "`bug_hash`, `summary`, `title`, `harness_revision`, `relevant_functions`, `triage`, "
                    "`duplicate_of`):\n"
                    f"{history_lines}"
                )
            }
        )

    ctx.append(
        {
            "role": "user",
            "content": f'Testcase: \n```\n{testcase}\n```\nCrash report: \n```\n{report}\n```'
        }
    )

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
                    "triage": {
                        "type": "object",
                        "description": "Structured security-focused triage fields (see developer prompt).",
                        "properties": {
                            "api_usage": { "type": "string" },
                            "misuse_category": { "type": "array", "items": { "type": "string" } },
                            "precondition_documented": { "type": "string" },
                            "why_not_intended": { "type": "string" },

                            "trigger_surface": { "type": "string" },
                            "requires_crafted_data": { "type": "boolean" },
                            "requires_unusual_sequence": { "type": "boolean" },
                            "reproducibility": { "type": "string" },
                            "attacker_control": { "type": "string" },

                            "crash_mechanism": { "type": "string" },
                            "memory_safety": { "type": "string" },
                            "debug_only": { "type": "boolean" },

                            "exploitation_likelihood": { "type": "string" },
                            "security_impact": { "type": "string" },
                            "why_exploitable_or_not": { "type": "string" },
                            "confidence": { "type": "number" },

                            "report_recommendation": { "type": "string" },
                            "report_priority": { "type": "string" },
                            "report_rationale": { "type": "string" },

                            "priority_score_llm": { "type": "integer" }
                        },
                        "required": [
                            "api_usage",
                            "misuse_category",
                            "precondition_documented",
                            "trigger_surface",
                            "requires_crafted_data",
                            "requires_unusual_sequence",
                            "reproducibility",
                            "attacker_control",
                            "crash_mechanism",
                            "memory_safety",
                            "debug_only",
                            "exploitation_likelihood",
                            "security_impact",
                            "confidence",
                            "report_recommendation",
                            "report_priority",
                            "report_rationale",
                            "priority_score_llm"
                        ]
                    },
                    "minimized_testcase": { "type": "string", "description": "A minimized C++ testcase that demonstrates the crash" },
                    "explanation": { "type": "string", "description": "A detailed explanation of your assessment" },
                    "can_patch_fuzzer": { "type": "boolean", "description": "Whether the fuzzer can be patched to fix or avoid the crash" },
                    "patch_guidance": { "type": "string", "description": "Detailed suggestions on how to patch the fuzzer to fix or avoid the crash (if `can_patch_fuzzer` is `true`)" },
                    "relevant_functions": { "type": "array", "description": "The list of functions which are relevant to the crash", "items": { "type": "string" } },
                    "duplicate_of": { "type": "string", "description": "If this crash is functionally the same as a previous crash, set this to that crash's `bug_hash` / `bucket_hash` from the previous crash list; otherwise omit or leave empty." },
                    "duplicate_reason": { "type": "string", "description": "If `duplicate_of` is set, a short explanation of why this crash is considered a duplicate of the referenced prior crash." }
                },
                "required": ["title", "triage", "minimized_testcase", "explanation", "can_patch_fuzzer", "patch_guidance", "relevant_functions"]
            }
        }
    ]

    test_iterations = []

    for i in range(MAX_ITERATIONS):
        print(f'{Colors.GREEN}[+]{Colors.END} Iteration {i+1} of {MAX_ITERATIONS}')
        response = client.responses.parse(
            model='gpt-5',
            input=ctx,
            tools=tools,
            reasoning={
                "effort": "low",
                "summary": "auto"
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

                        test_iterations.append(CrashAssessmentTest(testcase=testcase, result=output))

                        ctx.append({
                            "type": "function_call_output",
                            "call_id": message.call_id,
                            "output": output.model_dump_json()
                        })
                    elif message.name == 'crash_assessment':
                        assessment = CrashAssessment.model_validate_json(message.arguments)
                        # Fill deterministic score for stable ranking/comparison.
                        assessment.triage.priority_score_computed = compute_priority_score(assessment.triage)
                        assessment.usage = usage
                        assessment.test_iterations = test_iterations
                        print(
                            f'{Colors.GREEN}[+]{Colors.END} Crash assessment: '
                            f'({assessment.triage.report_priority}, score={assessment.triage.priority_score_computed}) '
                            f'{assessment.title}'
                        )
                        return assessment
                    else:
                        print(f'{Colors.RED}[-]{Colors.END} Unknown tool call: {message.name}')

        # If we are on the second to last iteration, tell the model to use the crash_assessment tool
        if i == MAX_ITERATIONS - 2:
            ctx.append({
                "role": "user",
                "content": PROMPT_DISTILL_AGENT_LAST
            })
            

    print(f'{Colors.RED}[-]{Colors.END} No crash assessment found')
    return CrashAssessment(
        title=summary,
        triage=CrashTriage(
            report_rationale="The agent did not produce a valid assessment tool call; insufficient information to assess legitimacy/exploitability.",
            priority_score_llm=0,
            priority_score_computed=0,
        ),
        minimized_testcase=testcase,
        explanation='',
        usage=usage,
        can_patch_fuzzer=False,
        patch_guidance=None,
        relevant_functions=fuzzer_stubs,
        test_iterations=test_iterations
    )


def main(args):
    harness = ProjectMetadata.model_validate_json(open(args.harness).read())
    testcase = open(args.external_code).read()
    history = None
    if getattr(args, "history", None):
        try:
            history = json.loads(open(args.history).read())
        except Exception:
            history = None

    assessment = run_distill_agent(harness, testcase, history=history)
    print(f'{Colors.GREEN}[+]{Colors.END} Usage: {assessment.usage.cost_by_task()}')
    open(args.output, 'w').write(assessment.model_dump_json(indent=2))


def register(subparser: ArgumentParser):
    parser = subparser.add_parser('distill-agent')
    parser.add_argument('--harness', type=str, required=True)
    parser.add_argument('--external-code', type=str, required=True)
    parser.add_argument('--output', type=str, required=True)
    parser.add_argument('--history', type=str, required=False, help='Optional JSON file with a list of previous crash summaries')
    parser.set_defaults(func=main)
