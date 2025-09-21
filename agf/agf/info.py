from pathlib import Path
import json
from datetime import datetime
from typing import List, Optional

from .utils.colors import Colors
from .utils.project import Project
from .data.metadata import MODEL_COST


def do_info(args):
    path = Path(args.path)
    project = Project(path)
    if not project.exists() or not project.has_config():
        print(f'[{Colors.RED}-{Colors.END}] Could not find project at {path.absolute()}')
        return

    # Get all harnesses
    harnesses = project.get_harnesses()
    if not harnesses:
        print(f'[{Colors.YELLOW}!{Colors.END}] No harnesses found in project')
        return

    print(f'[{Colors.GREEN}+{Colors.END}] Found {Colors.BOLD}{Colors.LIGHT_CYAN}{len(harnesses)}{Colors.END} harnesses')
    
    for harness in harnesses:
        harness_name = harness.path.name
        print(f'\n[{Colors.CYAN}*{Colors.END}] {Colors.BOLD}{Colors.UNDERLINE}Harness:{Colors.END} {Colors.LIGHT_PURPLE}{harness_name}{Colors.END}')
        
        # Print total inference cost
        total_cost = harness.cost()
        if total_cost > 0:
            print(f'  [{Colors.GREEN}+{Colors.END}] {Colors.BOLD}Total inference cost:{Colors.END} {Colors.LIGHT_GREEN}${total_cost:.4f}{Colors.END}')
        else:
            print(f'  [{Colors.YELLOW}!{Colors.END}] {Colors.DIM}No cost information available{Colors.END}')
        
        # Check if there's a campaign
        campaign_dir = harness.campaign_dir()
        if campaign_dir.exists() and (campaign_dir / 'reports').exists():
            reports_dir = campaign_dir / 'reports'
            reports = []
            
            # Collect all reports
            for report_dir in reports_dir.iterdir():
                if report_dir.is_dir():
                    info_file = report_dir / 'info.json'
                    if info_file.exists():
                        try:
                            with open(info_file, 'r') as f:
                                crash_info = json.load(f)
                            reports.append((report_dir, crash_info))
                        except (json.JSONDecodeError, KeyError):
                            continue
            
            if reports:
                # Sort by harness revision
                reports.sort(key=lambda x: x[1].get('harness_revision', 0))
                
                print(f'  [{Colors.GREEN}+{Colors.END}] {Colors.BOLD}{Colors.UNDERLINE}Campaign reports{Colors.END} ({Colors.LIGHT_CYAN}{len(reports)}{Colors.END} found):')
                
                for report_dir, crash_info in reports:
                    report_name = report_dir.name
                    harness_revision = crash_info.get('harness_revision', 'unknown')
                    summary = crash_info.get('summary', 'No summary')
                    
                    print(f'    [{Colors.CYAN}*{Colors.END}] {Colors.BOLD}Report:{Colors.END} {Colors.LIGHT_PURPLE}{report_name}{Colors.END} (revision {Colors.LIGHT_YELLOW}{harness_revision}{Colors.END})')
                    print(f'      {Colors.BOLD}Summary:{Colors.END} {Colors.LIGHT_WHITE}{summary}{Colors.END}')
                    
                    # Check for assessment
                    assessment_file = report_dir / 'assessment.json'
                    if assessment_file.exists():
                        try:
                            with open(assessment_file, 'r') as f:
                                assessment_data = json.load(f)
                            
                            # Extract cost from assessment if available
                            assessment_cost = 0.0
                            if 'usage' in assessment_data and 'by_task' in assessment_data['usage']:
                                usage_data = assessment_data['usage']
                                for task, metrics in usage_data['by_task'].items():
                                    if 'models' in metrics:
                                        for model, tokens in metrics['models'].items():
                                            if model in MODEL_COST:
                                                input_price, output_price = MODEL_COST[model]
                                                input_tokens = tokens.get('input_tokens', 0)
                                                output_tokens = tokens.get('output_tokens', 0)
                                                assessment_cost += (input_tokens / 1_000_000) * input_price + (output_tokens / 1_000_000) * output_price
                            
                            crash_type = assessment_data.get('crash_type', 'unknown')
                            can_patch = assessment_data.get('can_patch_fuzzer', False)
                            
                            patchable_color = Colors.LIGHT_GREEN if can_patch else Colors.LIGHT_RED
                            print(f'      {Colors.BOLD}Assessment:{Colors.END} {Colors.LIGHT_CYAN}{crash_type}{Colors.END} (patchable: {patchable_color}{can_patch}{Colors.END})')
                            if assessment_cost > 0:
                                print(f'      {Colors.BOLD}Assessment cost:{Colors.END} {Colors.LIGHT_GREEN}${assessment_cost:.4f}{Colors.END}')
                            
                        except (json.JSONDecodeError, KeyError) as e:
                            print(f'      {Colors.BOLD}Assessment:{Colors.END} {Colors.RED}Error reading assessment file - {e}{Colors.END}')
                    
                    # Check for patch
                    patch_file = report_dir / 'patch.json'
                    if patch_file.exists():
                        try:
                            with open(patch_file, 'r') as f:
                                patch_data = json.load(f)
                            
                            # Extract cost from patch if available
                            patch_cost = 0.0
                            if 'usage' in patch_data and 'by_task' in patch_data['usage']:
                                usage_data = patch_data['usage']
                                for task, metrics in usage_data['by_task'].items():
                                    if 'models' in metrics:
                                        for model, tokens in metrics['models'].items():
                                            if model in MODEL_COST:
                                                input_price, output_price = MODEL_COST[model]
                                                input_tokens = tokens.get('input_tokens', 0)
                                                output_tokens = tokens.get('output_tokens', 0)
                                                patch_cost += (input_tokens / 1_000_000) * input_price + (output_tokens / 1_000_000) * output_price
                            
                            print(f'      {Colors.BOLD}Patch cost:{Colors.END} {Colors.LIGHT_GREEN}${patch_cost:.4f}{Colors.END}')
                            
                        except (json.JSONDecodeError, KeyError) as e:
                            print(f'      {Colors.BOLD}Patch:{Colors.END} {Colors.LIGHT_RED}Error reading patch file - {e}{Colors.END}')
            else:
                print(f'  [{Colors.YELLOW}!{Colors.END}] {Colors.DIM}No reports found in campaign{Colors.END}')
        else:
            print(f'  [{Colors.YELLOW}!{Colors.END}] {Colors.DIM}No campaign found{Colors.END}')

    

def register(subparsers):
    subparser = subparsers.add_parser('info', help='Display information about the project')
    subparser.add_argument('path', nargs='?', help='The path to the project', default='.')
    subparser.add_argument('--name', type=str, help='Show synthesis events for a specific harness')
    subparser.add_argument('--cost', action='store_true', help='Show detailed cost breakdowns per task and totals')
    subparser.set_defaults(func=do_info)
