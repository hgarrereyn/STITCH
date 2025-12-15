from pathlib import Path
import difflib
from ..data.project_info import ProjectInfo
from .looper import CrashInfo
from pydantic import BaseModel
from .distill_agent import CrashAssessment, OldCrashAssessment
from typing import Optional
from .patcher import PatchInfo
from ..utils.colors import Colors
from .pretty import pretty_print_code



class BugReport(BaseModel):
    info: CrashInfo
    assessment: Optional[CrashAssessment | OldCrashAssessment] = None
    patch: Optional[PatchInfo] = None


def main(args):
    campaign = Path(args.campaign)

    crashes: list[BugReport] = []

    reports = campaign / 'reports'
    for report_dir in reports.iterdir():
        if report_dir.is_dir():
            p_info = report_dir / 'info.json'
            p_assessment = report_dir / 'assessment.json'
            p_patch = report_dir / 'patch.json'
            
            bug = BugReport(info=CrashInfo.model_validate_json(open(p_info).read()))
            if p_assessment.exists():
                bug.assessment = CrashAssessment.model_validate_json(open(p_assessment).read())
            if p_patch.exists():
                bug.patch = PatchInfo.model_validate_json(open(p_patch).read())
            crashes.append(bug)

    print(f'{Colors.GREEN}[+]{Colors.END} Found {len(crashes)} bugs')

    # Sort by harness revision and print pretty
    crashes.sort(key=lambda x: x.info.harness_revision)
    for crash in crashes:
        print(f'{Colors.GREEN}[+]{Colors.END} Bug in revision {crash.info.harness_revision}: {crash.info.summary}')
        print(f'    {Colors.CYAN}[+]{Colors.END} Original testcase: {crash.info.original_testcase}')
        if crash.assessment:
            print(f'    {Colors.CYAN}[+]{Colors.END} Assessment: {crash.assessment.title}')
            if hasattr(crash.assessment, "triage"):
                print(f'    {Colors.CYAN}[+]{Colors.END} Crash mechanism: {crash.assessment.triage.crash_mechanism}')
                print(f'    {Colors.CYAN}[+]{Colors.END} Priority: {crash.assessment.triage.report_priority}')
            else:
                print(f'    {Colors.CYAN}[+]{Colors.END} Crash type: {crash.assessment.crash_type}')
            if getattr(crash.assessment, "duplicate_of", None):
                print(f'    {Colors.CYAN}[+]{Colors.END} Duplicate of: {crash.assessment.duplicate_of}')
            print(f'    {Colors.CYAN}[+]{Colors.END} Can patch fuzzer: {crash.assessment.can_patch_fuzzer}')
            print(f'    {Colors.CYAN}[+]{Colors.END} Description:')
            pretty_print_code(crash.assessment.explanation, language="markdown")
            print(f'    {Colors.CYAN}[+]{Colors.END} Minimized testcase:')
            pretty_print_code(crash.assessment.minimized_testcase)
        if crash.patch:
            print(f'    {Colors.CYAN}[+]{Colors.END} Patch:')
            # Print the diffs for each modified stub
            original_stubs = {s.name: s for s in crash.patch.original_stubs}
            modified_stubs = {s.name: s for s in crash.patch.modified_stubs}
            for name, stub in modified_stubs.items():
                print(f'{Colors.GREEN}[+]{Colors.END} Modified stub for {Colors.YELLOW}{name}{Colors.END}')
                diff_lines = list(difflib.unified_diff(
                    original_stubs[name].code.splitlines(),
                    stub.code.splitlines(),
                    lineterm='',
                    n=100,
                ))
                diff_code = '\n'.join(diff_lines)
                pretty_print_code(diff_code, language="diff", title=f"Modified stub for {name}")
        


def register(subparsers):
    parser = subparsers.add_parser('campaign-info')
    parser.add_argument('--campaign', type=str, required=True, help='Path to the campaign directory')
    parser.set_defaults(func=main)
