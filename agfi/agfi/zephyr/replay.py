from pathlib import Path
import difflib
from ..data.project_info import ProjectInfo
from .looper import CrashInfo
from pydantic import BaseModel
from .distill_agent import CrashAssessment
from typing import Optional
from .patcher import PatchInfo
from ..utils.colors import Colors
from .pretty import pretty_print_code
from ..utils.crash_repro import build_and_observe_crash


class BugReport(BaseModel):
    info: CrashInfo
    assessment: Optional[CrashAssessment] = None
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

    for crash in crashes:
        if crash.assessment is None:
            continue

        # print info about the crash summary and crash type
        print(f'{Colors.GREEN}[+]{Colors.END} Crash: {crash.info.summary} ({crash.assessment.crash_type})')
        print(f'    {Colors.CYAN}[+]{Colors.END} Minimized testcase:')
        if args.raw:
            print(crash.assessment.minimized_testcase)
        else:
            pretty_print_code(crash.assessment.minimized_testcase)

        # Recompile the minimized test program and see if it still crashes
        report, summary = build_and_observe_crash(str(crash.assessment.minimized_testcase))
        if report is None:
            print(f'{Colors.RED}[-]{Colors.END} no crash observed')
        else:
            print(f'{Colors.GREEN}[+]{Colors.END} crash observed:')
            if args.raw:
                print(report)
            else:
                pretty_print_code(report, language="json", title="crash report")
            print(f'{Colors.CYAN}[+]{Colors.END} crash summary:')
            print(summary)


def register(subparsers):
    parser = subparsers.add_parser('replay')
    parser.add_argument('campaign', type=str, help='Path to the campaign directory')
    parser.add_argument('--raw', action='store_true', help='Print the raw crash report and summary')
    parser.set_defaults(func=main)
