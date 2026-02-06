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
from ..utils.crash_repro import build_and_observe_crash


class BugReport(BaseModel):
    info: CrashInfo
    assessment: Optional[CrashAssessment | OldCrashAssessment] = None
    patch: Optional[PatchInfo] = None


def main(args):
    campaign = Path(args.campaign)
    bugs_dir = Path(args.bugs)
    crashes: list[BugReport] = []

    reports = campaign / 'reports'
    for report_dir in reports.iterdir():
        if report_dir.is_dir():
            p_info = report_dir / 'info.json'
            p_assessment = report_dir / 'assessment.json'
            p_patch = report_dir / 'patch.json'
            
            bug = BugReport(info=CrashInfo.model_validate_json(open(p_info).read()))
            if p_assessment.exists():
                try:
                    bug.assessment = CrashAssessment.model_validate_json(open(p_assessment).read())
                except:
                    bug.assessment = OldCrashAssessment.model_validate_json(open(p_assessment).read())
            if p_patch.exists():
                bug.patch = PatchInfo.model_validate_json(open(p_patch).read())
            crashes.append(bug)

    if args.bug_hash is not None:
        crashes = [crash for crash in crashes if crash.info.bucket_hash == args.bug_hash]

    for crash in crashes:
        if crash.assessment is None:
            continue

        # print info about the crash summary and crash type
        if isinstance(crash.assessment, CrashAssessment):
            print(
                f'{Colors.GREEN}[+]{Colors.END} Crash: ({crash.info.bucket_hash}) '
                f'{crash.info.summary} ({crash.assessment.triage.crash_mechanism}, {crash.assessment.triage.report_priority})'
            )
            print(f'    {Colors.CYAN}[+]{Colors.END} Report rationale: {crash.assessment.triage.report_rationale}')
        else:
            print(f'{Colors.GREEN}[+]{Colors.END} Crash: ({crash.info.bucket_hash}) {crash.info.summary} ({crash.assessment.crash_type})')
            print(f'    {Colors.CYAN}[+]{Colors.END} Bug report: {crash.assessment.crash_type_reasoning}')
        print(f'    {Colors.CYAN}[+]{Colors.END} Description:')
        pretty_print_code(crash.assessment.explanation, language="markdown")
        print(f'    {Colors.CYAN}[+]{Colors.END} Minimized testcase:')
        if args.raw:
            print(crash.assessment.minimized_testcase)
        else:
            pretty_print_code(crash.assessment.minimized_testcase)

        if args.save == 0:
            path = bugs_dir / f'{crash.info.bucket_hash}.cpp'
            path.write_text(str(crash.assessment.minimized_testcase))
            print(f'{Colors.GREEN}[+]{Colors.END} Saved testcase to {path.absolute()}')

        # Recompile the minimized test program and see if it still crashes
        crash_res = build_and_observe_crash(str(crash.assessment.minimized_testcase), with_output=True)
        if crash_res is not None:
            obs, stdout, stderr = crash_res
            report, summary = obs
            if stdout != '':
                pretty_print_code(stdout, language="text", title="stdout")
            if stderr != '':
                pretty_print_code(stderr, language="text", title="stderr")
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
        else:
            print(f'{Colors.RED}[0]{Colors.END} failed to build and observe crash')

        if args.all:
            match crash.assessment:
                case CrashAssessment():
                    i = 0
                    for iteration in crash.assessment.test_iterations:
                        i += 1
                        print(f'{Colors.CYAN}[+]{Colors.END} iteration {i}')
                        if args.save == i:
                            path = bugs_dir / f'{crash.info.bucket_hash}.cpp'
                            path.write_text(str(iteration.testcase))
                            print(f'{Colors.GREEN}[+]{Colors.END} Saved testcase to {path.absolute()}')
                        testcase = iteration.testcase
                        if args.raw:
                            print(testcase)
                        else:
                            pretty_print_code(testcase, language="cpp", title=f"testcase {i}")
                        crash_res = build_and_observe_crash(str(testcase), with_output=True)
                        if crash_res is not None:
                            obs, stdout, stderr = crash_res
                            report, summary = obs
                            if stdout != '':
                                pretty_print_code(stdout, language="text", title="stdout")
                            if stderr != '':
                                pretty_print_code(stderr, language="text", title="stderr")
                            if report is None:
                                print(f'{Colors.RED}[-]{Colors.END} no crash observed for iteration {i}')
                            else:
                                print(f'{Colors.GREEN}[+]{Colors.END} crash observed for iteration {i}:')
                                if args.raw:
                                    print(report)
                                else:
                                    pretty_print_code(report, language="json", title="crash report")
                                print(f'{Colors.CYAN}[+]{Colors.END} crash summary:')
                                print(summary)
                        else:
                            print(f'{Colors.RED}[-]{Colors.END} no crash observed for iteration {i}')
                case _:
                    # TODO: remove with older version
                    print(f'{Colors.RED}[-]{Colors.END} no test iterations to replay')


def register(subparsers):
    parser = subparsers.add_parser('replay')
    parser.add_argument('campaign', type=str, help='Path to the campaign directory')
    parser.add_argument('bugs', type=str, help='Path to the bugs directory')
    parser.add_argument('--raw', action='store_true', help='Print the raw crash report and summary')
    parser.add_argument('-b', '--bug-hash', type=str, help='The hash of the bug to replay', default=None)
    parser.add_argument('-a', '--all', action='store_true', help='Replay all iterations of the testcase', default=False)
    parser.add_argument('-s', '--save', type=int, default=None, help='If specified, save the testcase to disk.')
    parser.set_defaults(func=main)
