from typing import Optional
from pydantic import BaseModel
from pathlib import Path
import tempfile
import subprocess
import json

from langchain_openai import ChatOpenAI
from ..utils.crash_repro import build_and_observe_crash

class MinimizeResult(BaseModel):
    testcase: str
    report: str


class MinimizePrediction(BaseModel):
    cpp_file: str


PROMPT_MINIMIZE = """
You are an expert C/C++ crash analyzer and minimizer.

You will be given a cpp file that triggers a crash in a library along with a crash report.
This file was generated automatically by a fuzzer and may contain extraneous code.

Your objective is to minimize the file by removing extraneous code and reformatting code, so that the file is as small as possible while still triggering the crash.

Follow these guidelines:

[GUIDELINES]
Your response should be a new raw cpp file, without ``` or ```cpp.

When minimizing, DO the following:
- remove dead code
- reformat code to be more readable and canonical
- rename variables to be more descriptive
- simplify values and expressions
- remove error checking code (e.g. `if (...) FUZZ_BAIL()`) if it is not necessary to trigger the crash

When minimizing, DO NOT do the following:
- do not remove headers that were included in the original file
- do not remove code that is necessary to trigger the crash
- do not introduce any new functionality that was not present in the original file
[/GUIDELINES]

[CRASH_REPORT]
{crash_report}
[/CRASH_REPORT]

[ORIGINAL_FILE]
{original_file}
[/ORIGINAL_FILE]
"""


def try_minimize(crash_file: str, crash_report: str, summary: str, model: str) -> Optional[MinimizeResult]:
    # Run the llm
    llm = ChatOpenAI(model=model).with_structured_output(MinimizePrediction, strict=True)
    res: MinimizePrediction = llm.invoke(PROMPT_MINIMIZE.format(crash_report=crash_report, original_file=crash_file))
    new_file = res.cpp_file

    # Validate the new file
    report, new_summary = build_and_observe_crash(new_file)
    if report is None:
        print('[!] Minimize failed: no crash observed')
        return None
    
    if summary != new_summary:
        print('[!] Minimize failed: summary changed')
        print(f'Old summary: {summary}')
        print(f'New summary: {new_summary}')
        return None
    
    return MinimizeResult(testcase=new_file, report=report)


def run_minimize(args):
    crash_file = open(args.crash_file).read()
    
    # Initial triage
    crash_report, summary = build_and_observe_crash(crash_file)
    if crash_report is None:
        print('[!] Initial triage failed: no crash observed')
        return
    
    curr_crash = crash_file
    curr_report = crash_report
    curr_step = 0
    curr_size = len(curr_crash)
    did_minimize = False

    print(f'Initial crash file size: {curr_size} bytes')

    stuck_steps = 0
    while stuck_steps < args.stuck_steps:
        print(f'[Step {curr_step}] {curr_size} bytes')
        curr_step += 1

        # Try to minimize the crash file
        res = try_minimize(curr_crash, curr_report, summary, args.model)
        if res is None:
            print('[!] Minimize failed')
            stuck_steps += 1
            continue
        elif len(res.testcase) >= curr_size:
            print('[!] Minimize failed: size increased')
            stuck_steps += 1
            continue
        
        stuck_steps = 0
        curr_crash = res.testcase
        curr_report = res.report
        curr_size = len(curr_crash)

        print(f'[+] Minimize success: {curr_size} bytes')
        print('-' * 80)
        print(res.testcase)
        print('-' * 80)

        did_minimize = True

    if did_minimize:
        if args.out_file is not None:
            with open(args.out_file, 'w') as f:
                f.write(curr_crash)
        if args.out_report is not None:
            with open(args.out_report, 'w') as f:
                f.write(curr_report)
    else:
        print('[-] Minimize failed: no output file produced')


def register(subparsers):
    parser = subparsers.add_parser('llm-minimize', help='Minimize a crash file using an LLM')
    parser.add_argument('crash_file', type=str, help='The crash file to minimize')
    parser.add_argument('--out_file', type=str, help='The file to write the minimized crash file to')
    parser.add_argument('--out_report', type=str, help='The file to write the minimized crash report to')
    parser.add_argument('--model', type=str, default='gpt-4.1', help='The model to use for minimization')
    parser.add_argument('--stuck_steps', type=int, default=3, help='The maximum number of failed steps to take before giving up')
    parser.set_defaults(func=run_minimize)
