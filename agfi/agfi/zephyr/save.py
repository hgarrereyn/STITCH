from pathlib import Path
import difflib
from ..data.project_info import ProjectInfo
from .looper import CrashInfo
from pydantic import BaseModel
from .distill_agent import CrashAssessment, OldCrashAssessment
from typing import Optional
from .patcher import PatchInfo
from ..utils.colors import Colors


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

    if len(crashes) == 0:
        print(f'{Colors.RED}[-]{Colors.END} Bug hash {args.bug_hash} not found')
        return

    crash = crashes[0]

    if crash.assessment is None:
        print(f'{Colors.RED}[-]{Colors.END} No assessment found for bug hash {args.bug_hash}')
        exit(1)

    # Save the assessment to <bugs_dir>/<bug_hash>_assessment.json
    path = bugs_dir / f'{crash.info.bucket_hash}_assessment.json'
    path.write_text(crash.assessment.model_dump_json(indent=2))
    print(f'{Colors.GREEN}[+]{Colors.END} Saved assessment to {path.absolute()}')

    if args.save == 0:
        path = bugs_dir / f'{crash.info.bucket_hash}.cpp'
        path.write_text(str(crash.assessment.minimized_testcase))
        print(f'{Colors.GREEN}[+]{Colors.END} Saved testcase to {path.absolute()}')
    else:
        match crash.assessment:
            case CrashAssessment():
                if (args.save - 1) >= len(crash.assessment.test_iterations):
                    print(f'{Colors.RED}[-]{Colors.END} Save index {args.save} is out of range')
                    exit(1)
                iteration = crash.assessment.test_iterations[args.save - 1]
                path = bugs_dir / f'{crash.info.bucket_hash}.cpp'
                path.write_text(str(iteration.testcase))
                print(f'{Colors.GREEN}[+]{Colors.END} Saved testcase to {path.absolute()}')
            case _:
                print(f'{Colors.RED}[-]{Colors.END} No test iterations to save')
                exit(1)


def register(subparsers):
    parser = subparsers.add_parser('save')
    parser.add_argument('campaign', type=str, help='Path to the campaign directory')
    parser.add_argument('bugs', type=str, help='Path to the bugs directory')
    parser.add_argument('-b', '--bug-hash', type=str, help='The hash of the bug to save', default=None)
    parser.add_argument('-s', '--save', type=int, default=0, help='If specified, save the testcase to disk.')
    parser.set_defaults(func=main)
