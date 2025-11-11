from pathlib import Path
import json
from typing import List, Dict, Tuple, Optional
from collections import defaultdict

from .utils.colors import Colors
from .utils.project import Project


class BugReport:
    def __init__(self, report_dir: Path):
        self.report_dir = report_dir
        self._info = None
        self._assessment = None
        self.status = None
    
    @property
    def info_path(self) -> Path:
        return self.report_dir / 'info.json'
    
    @property
    def assessment_path(self) -> Path:
        return self.report_dir / 'assessment.json'
    
    @property
    def info(self):
        if self._info is None and self.info_path.exists():
            try:
                with open(self.info_path, 'r') as f:
                    self._info = json.load(f)
            except:
                pass
        return self._info
    
    @property
    def assessment(self):
        if self._assessment is None and self.assessment_path.exists():
            try:
                with open(self.assessment_path, 'r') as f:
                    self._assessment = json.load(f)
            except:
                pass
        return self._assessment
    
    @property
    def severity(self) -> str:
        if self.assessment:
            return self.assessment.get('crash_type', 'unknown')
        return 'unknown'
    
    @property
    def title(self) -> str:
        if self.assessment:
            return self.assessment.get('title', 'Untitled')
        return 'Unknown'
    
    @property
    def bucket_hash(self) -> str:
        if self.info:
            return self.info.get('bucket_hash', 'unknown')
        return 'unknown'
    
    def matches_severity_filter(self, severity_filter: Optional[str]) -> bool:
        """Check if this report matches the severity filter."""
        if severity_filter is None:
            return True
        
        severity_lower = self.severity.lower()
        
        # Filter by level (crit, high, med, low)
        if severity_filter.lower() in ['crit', 'critical']:
            return 'crit' in severity_lower
        elif severity_filter.lower() in ['high']:
            return 'high' in severity_lower
        elif severity_filter.lower() in ['med', 'medium']:
            return 'med' in severity_lower
        elif severity_filter.lower() in ['low']:
            return 'low' in severity_lower
        elif severity_filter.lower() == 'invalid':
            return severity_lower.startswith('invalid')
        elif severity_filter.lower() == 'valid':
            return severity_lower.startswith('valid')
        
        # If filter doesn't match any known pattern, include it
        return True


def get_severity_color(severity: str) -> str:
    """Get the color code for a severity level."""
    severity_lower = severity.lower()
    
    if severity_lower.startswith('invalid'):
        return Colors.RED
    elif severity_lower.startswith('valid'):
        # Extract the level from valid-{type}-{level}
        if '-crit' in severity_lower:
            return Colors.LIGHT_RED
        elif '-high' in severity_lower:
            return Colors.YELLOW
        elif '-med' in severity_lower:
            return Colors.CYAN
        elif '-low' in severity_lower:
            return Colors.GREEN
        else:
            # Unknown valid severity, use default
            return Colors.LIGHT_CYAN
    else:
        # Unknown severity format, use default
        return Colors.CYAN


def do_multi(args):
    """List all bug reports across multiple projects."""
    path = Path(args.path)
    
    if not path.is_dir():
        print(f'[{Colors.RED}-{Colors.END}] Path {path.absolute()} is not a directory')
        return
    
    # Find all projects
    projects = []
    for subdir in path.iterdir():
        if not subdir.is_dir():
            continue
        
        # Check if this is a project (has agf.json)
        agf_json = subdir / 'agf.json'
        if agf_json.exists():
            try:
                project = Project(subdir)
                if project.has_config():
                    projects.append((subdir, project))
            except:
                pass
    
    if len(projects) == 0:
        print(f'[{Colors.YELLOW}!{Colors.END}] No projects found in {path.absolute()}')
        return
    
    print(f'[{Colors.GREEN}+{Colors.END}] Found {len(projects)} projects\n')
    
    # Collect all bug reports
    all_reports: Dict[str, Dict[str, List[BugReport]]] = defaultdict(lambda: defaultdict(list))
    
    for project_path, project in projects:
        project_name = project_path.name
        
        # Load status.json if it exists
        status_file = project.status_file()
        status_data = {}
        if status_file.exists():
            try:
                with open(status_file, 'r') as f:
                    status_data = json.load(f)
            except:
                pass
        
        # Get all harnesses
        harnesses = project.get_harnesses()
        
        if len(harnesses) == 0:
            continue
        
        for harness in harnesses:
            harness_name = harness.path.name
            
            # Look for bug reports in campaign/reports directory
            campaign_dir = harness.campaign_dir()
            reports_dir = campaign_dir / 'reports'
            
            if not reports_dir.exists():
                continue
            
            # Find all report directories
            for report_dir in reports_dir.iterdir():
                if not report_dir.is_dir():
                    continue
                
                report = BugReport(report_dir)
                
                # Only include reports that have assessments
                if report.assessment is not None:
                    # Check if this bug has a status
                    status_key = report.bucket_hash
                    if status_key in status_data:
                        status_value = status_data[status_key]
                        if isinstance(status_value, str):
                            report.status = status_value
                        else:
                            report.status = 'unknown'
                    else:
                        report.status = None
                    
                    # Filter out invalid bugs unless --show-invalid is specified
                    if not args.show_invalid and report.severity.lower().startswith('invalid'):
                        continue
                    
                    # Apply severity filter if specified
                    if not report.matches_severity_filter(args.severity):
                        continue
                    
                    all_reports[project_name][harness_name].append(report)
    
    # Print the results
    if len(all_reports) == 0:
        print(f'[{Colors.YELLOW}!{Colors.END}] No bug reports found')
        return
    
    # Organize by severity
    total_reports = 0
    for project_name in sorted(all_reports.keys()):
        project_reports = all_reports[project_name]

        # Find the project config
        project_path = Path(args.path) / project_name
        project = Project(project_path)
        project_config = project.get_config()
        
        # Count total reports for this project
        project_count = sum(len(reports) for reports in project_reports.values())
        total_reports += project_count
        
        print(f'{Colors.CYAN}{project_name}{Colors.END} ({project_count} reports) {Colors.DIM}{project_config.repo}{Colors.END}')
        
        for harness_name in sorted(project_reports.keys()):
            harness_reports = project_reports[harness_name]
            
            # Group by severity
            by_severity = defaultdict(list)
            for report in harness_reports:
                by_severity[report.severity].append(report)
            
            print(f'  {Colors.DIM}{harness_name}{Colors.END}')
            
            for severity in sorted(by_severity.keys()):
                reports_for_severity = by_severity[severity]
                
                # Get severity color using the new function
                severity_color = get_severity_color(severity)
                
                print(f'    {severity_color}{severity}{Colors.END}: {len(reports_for_severity)}')
                
                # List individual reports
                for report in reports_for_severity:
                    # Determine status styling
                    status_prefix = '[   ]'
                    
                    if report.status:
                        if report.status.lower() == 'dup':
                            status_prefix = f'{Colors.DIM}[dup]{Colors.END} '
                        elif report.status.lower() == 'invalid':
                            status_prefix = f'{Colors.RED}[inv]{Colors.END} '
                        elif report.status.lower() == 'wontfix':
                            status_prefix = f'{Colors.YELLOW}[nof]{Colors.END} '
                        elif report.status.startswith('http'):
                            status_prefix = f'{Colors.GREEN}[sub]{Colors.END} '
                        else:
                            status_prefix = f'{Colors.DIM}[{report.status}]{Colors.END} '
                    
                    # Get title and short description
                    title = ''
                    if report.assessment:
                        title = report.assessment.get('title', '')

                    # Print bug info with hash and title
                    hash_color = severity_color
                    print(f'      [{hash_color}{report.bucket_hash}{Colors.END}]{status_prefix} {title}')
        
        print()
    
    print(f'{Colors.GREEN}Total: {total_reports} bug reports across {len(all_reports)} projects{Colors.END}')


def register(subparsers):
    subparser = subparsers.add_parser('multi', help='List bug reports across multiple projects')
    subparser.add_argument('path', nargs='?', help='The path containing multiple projects', default='.')
    subparser.add_argument('-v', '--verbose', action='store_true', help='Show individual bug titles', default=False)
    subparser.add_argument('--show-invalid', action='store_true', help='Include bugs marked as invalid', default=False)
    subparser.add_argument('--severity', type=str, help='Filter by severity level (crit, high, med, low, valid, invalid)', default=None)
    subparser.set_defaults(func=do_multi)

