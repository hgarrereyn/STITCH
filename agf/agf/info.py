from pathlib import Path
import json
from datetime import datetime
from typing import List, Optional

from .utils.colors import Colors
from .utils.project import Project


def format_timestamp(timestamp_str: str) -> str:
    """Format timestamp for display"""
    try:
        dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except:
        return timestamp_str


def get_event_icon(event_type: str) -> str:
    """Get colored icon for event type"""
    icons = {
        'synthesis_attempt': f'{Colors.CYAN}⟳{Colors.END}',
        'synthesis_success': f'{Colors.GREEN}✓{Colors.END}',
        'synthesis_failure': f'{Colors.RED}✗{Colors.END}',
        'validation_attempt': f'{Colors.BLUE}⟳{Colors.END}',
        'validation_success': f'{Colors.GREEN}✓{Colors.END}',
        'validation_failure': f'{Colors.RED}✗{Colors.END}',
        'regeneration_attempt': f'{Colors.YELLOW}⟳{Colors.END}',
    }
    return icons.get(event_type, f'{Colors.WHITE}?{Colors.END}')


def get_event_color(event_type: str) -> str:
    """Get color for event type"""
    colors = {
        'synthesis_attempt': Colors.CYAN,
        'synthesis_success': Colors.GREEN,
        'synthesis_failure': Colors.RED,
        'validation_attempt': Colors.BLUE,
        'validation_success': Colors.GREEN,
        'validation_failure': Colors.RED,
        'regeneration_attempt': Colors.YELLOW,
    }
    return colors.get(event_type, Colors.WHITE)


def display_events(events: List[dict], failed_only: bool = False, harness_data: dict = None, show_prompts: bool = False):
    """Display events in a beautiful format with colors"""
    if not events:
        print(f'[{Colors.YELLOW}!{Colors.END}] No events found in harness')
        return
    
    # Group events by function name
    functions = {}
    for event in events:
        func_name = event.get('function_name', 'unknown')
        if func_name not in functions:
            functions[func_name] = []
        functions[func_name].append(event)
    
    # Filter to only failed functions if requested
    if failed_only:
        failed_functions = {}
        for func_name, func_events in functions.items():
            # Check if this function ever had a successful validation
            has_successful_validation = any(
                event.get('event_type') == 'validation_success' 
                for event in func_events
            )
            if not has_successful_validation:
                failed_functions[func_name] = func_events
        
        functions = failed_functions
        
        if not functions:
            print(f'[{Colors.GREEN}+{Colors.END}] All endpoints were successfully validated!')
            return
        
        print(f'[{Colors.RED}+{Colors.END}] Failed Endpoints ({len(functions)} total - never successfully validated):')
    else:
        print(f'[{Colors.GREEN}+{Colors.END}] Synthesis Events ({len(events)} total):')
    
    print()
    
    # Create a lookup for function signatures from harness data
    function_signatures = {}
    if harness_data:
        # Look in validated_endpoints
        for func in harness_data.get('validated_endpoints', []):
            if isinstance(func, dict):
                name = func.get('name', '')
                inputs = func.get('inputs', [])
                outputs = func.get('outputs', [])
                function_signatures[name] = {'inputs': inputs, 'outputs': outputs}
        
        # Look in failed_endpoints
        for func in harness_data.get('failed_endpoints', []):
            if isinstance(func, dict):
                impl = func.get('impl', {})
                name = impl.get('name', '')
                inputs = impl.get('inputs', [])
                outputs = impl.get('outputs', [])
                function_signatures[name] = {'inputs': inputs, 'outputs': outputs}
    
    # Sort functions by name
    for func_name in sorted(functions.keys()):
        func_events = functions[func_name]
        
        # Display function name and signature
        print(f'  {Colors.BOLD}{Colors.CYAN}{func_name}{Colors.END}')
        
        # Display inputs and outputs if available
        if func_name in function_signatures:
            sig = function_signatures[func_name]
            inputs = sig.get('inputs', [])
            outputs = sig.get('outputs', [])
            
            if inputs:
                input_strs = []
                for inp in inputs:
                    if isinstance(inp, dict):
                        name = inp.get('name', 'unknown')
                        type_name = inp.get('type', 'unknown')
                        input_strs.append(f'{type_name} {name}')
                    else:
                        input_strs.append(str(inp))
                print(f'    {Colors.DIM}Inputs: {", ".join(input_strs)}{Colors.END}')
            
            if outputs:
                output_strs = []
                for out in outputs:
                    if isinstance(out, dict):
                        name = out.get('name', 'unknown')
                        type_name = out.get('type', 'unknown')
                        output_strs.append(f'{type_name} {name}')
                    else:
                        output_strs.append(str(out))
                print(f'    {Colors.DIM}Outputs: {", ".join(output_strs)}{Colors.END}')
        
        print()
        
        # Sort events by timestamp
        func_events.sort(key=lambda x: x.get('timestamp', ''))
        
        for event in func_events:
            event_type = event.get('event_type', 'unknown')
            timestamp = event.get('timestamp', '')
            attempt = event.get('attempt_number', 1)
            batch_id = event.get('batch_id', '')
            error_msg = event.get('error_message', '')
            validation_error = event.get('validation_error', '')
            cpp_code = event.get('cpp_code', '')
            tokens = event.get('tokens_used', '')
            model = event.get('model_used', '')
            
            # Format timestamp
            time_str = format_timestamp(timestamp)
            
            # Create event line
            icon = get_event_icon(event_type)
            color = get_event_color(event_type)
            
            line = f'    {icon} {color}{event_type.replace("_", " ").title()}{Colors.END}'
            if attempt > 1:
                line += f' (attempt {attempt})'
            line += f' at {Colors.DIM}{time_str}{Colors.END}'
            
            if batch_id:
                line += f' {Colors.DIM}[batch: {batch_id[:8]}...]{Colors.END}'
            
            if model:
                line += f' {Colors.DIM}[{model}]{Colors.END}'
            
            if tokens:
                line += f' {Colors.DIM}[{tokens} tokens]{Colors.END}'
            
            print(line)
            
            # Show error messages
            if error_msg:
                print(f'      {Colors.RED}Error: {error_msg}{Colors.END}')
            
            if validation_error:
                print(f'      {Colors.RED}Validation Error: {validation_error}{Colors.END}')
            
            # Show C++ code for successful synthesis
            if event_type == 'synthesis_success' and cpp_code:
                print(f'      {Colors.GREEN}C++ Code:{Colors.END}')
                code_lines = cpp_code.split('\n')
                for line in code_lines:
                    print(f'        {Colors.DIM}{line}{Colors.END}')
            
            # Show prompt and response data if requested
            if show_prompts:
                prompt_template = event.get('prompt_template', '')
                prompt_variables = event.get('prompt_variables', {})
                llm_response = event.get('llm_response', '')
                
                if prompt_template:
                    print(f'      {Colors.BLUE}Prompt Template:{Colors.END}')
                    # Truncate very long templates
                    template_lines = prompt_template.split('\n')
                    if len(template_lines) > 20:
                        for line in template_lines[:10]:
                            print(f'        {Colors.DIM}{line}{Colors.END}')
                        print(f'        {Colors.DIM}... ({len(template_lines) - 20} more lines) ...{Colors.END}')
                        for line in template_lines[-10:]:
                            print(f'        {Colors.DIM}{line}{Colors.END}')
                    else:
                        for line in template_lines:
                            print(f'        {Colors.DIM}{line}{Colors.END}')
                
                if prompt_variables:
                    print(f'      {Colors.BLUE}Prompt Variables:{Colors.END}')
                    for key, value in prompt_variables.items():
                        if key == 'functions' and isinstance(value, list):
                            print(f'        {Colors.DIM}{key}: {len(value)} functions{Colors.END}')
                        elif key == 'objects' and isinstance(value, list):
                            print(f'        {Colors.DIM}{key}: {len(value)} objects{Colors.END}')
                        elif key == 'previous_attempts' and isinstance(value, list):
                            print(f'        {Colors.DIM}{key}: {len(value)} previous attempts{Colors.END}')
                        elif isinstance(value, str) and len(value) > 100:
                            print(f'        {Colors.DIM}{key}: {value[:100]}...{Colors.END}')
                        else:
                            print(f'        {Colors.DIM}{key}: {value}{Colors.END}')
                
                if llm_response:
                    print(f'      {Colors.BLUE}LLM Response:{Colors.END}')
                    # Truncate very long responses
                    response_lines = llm_response.split('\n')
                    if len(response_lines) > 30:
                        for line in response_lines[:15]:
                            print(f'        {Colors.DIM}{line}{Colors.END}')
                        print(f'        {Colors.DIM}... ({len(response_lines) - 30} more lines) ...{Colors.END}')
                        for line in response_lines[-15:]:
                            print(f'        {Colors.DIM}{line}{Colors.END}')
                    else:
                        for line in response_lines:
                            print(f'        {Colors.DIM}{line}{Colors.END}')
        
        print()


def display_events_summary(events: List[dict], failed_only: bool = False):
    """Display a summary of events"""
    if not events:
        return
    
    # Group events by function name
    functions = {}
    for event in events:
        func_name = event.get('function_name', 'unknown')
        if func_name not in functions:
            functions[func_name] = []
        functions[func_name].append(event)
    
    # Filter to only failed functions if requested
    if failed_only:
        failed_functions = {}
        for func_name, func_events in functions.items():
            # Check if this function ever had a successful validation
            has_successful_validation = any(
                event.get('event_type') == 'validation_success' 
                for event in func_events
            )
            if not has_successful_validation:
                failed_functions[func_name] = func_events
        
        functions = failed_functions
        
        if not functions:
            print(f'[{Colors.GREEN}+{Colors.END}] All endpoints were successfully validated!')
            return
        
        # Count events by type for failed functions only
        failed_events = []
        for func_events in functions.values():
            failed_events.extend(func_events)
        
        event_counts = {}
        for event in failed_events:
            event_type = event.get('event_type', 'unknown')
            event_counts[event_type] = event_counts.get(event_type, 0) + 1
        
        print(f'[{Colors.RED}+{Colors.END}] Failed Endpoints Summary ({len(functions)} functions):')
        for event_type, count in sorted(event_counts.items()):
            color = get_event_color(event_type)
            print(f'  {color}{event_type.replace("_", " ").title()}{Colors.END}: {count}')
        
        print(f'  {Colors.RED}Failed Functions{Colors.END}: {", ".join(sorted(functions.keys()))}')
    else:
        # Count events by type for all events
        event_counts = {}
        for event in events:
            event_type = event.get('event_type', 'unknown')
            event_counts[event_type] = event_counts.get(event_type, 0) + 1
        
        print(f'[{Colors.GREEN}+{Colors.END}] Event Summary:')
        for event_type, count in sorted(event_counts.items()):
            color = get_event_color(event_type)
            print(f'  {color}{event_type.replace("_", " ").title()}{Colors.END}: {count}')
        
        # Show failed functions
        failed_functions = set()
        for event in events:
            event_type = event.get('event_type', '')
            if event_type in ['synthesis_failure', 'validation_failure']:
                failed_functions.add(event.get('function_name', 'unknown'))
        
        if failed_functions:
            print(f'  {Colors.RED}Failed Functions{Colors.END}: {", ".join(sorted(failed_functions))}')
    
    print()


def do_info(args):
    path = Path(args.path)
    project = Project(path)
    if not project.exists() or not project.has_config():
        print(f'[{Colors.RED}-{Colors.END}] Could not find project at {path.absolute()}')
        return

    # If name is provided, show events for that specific harness
    if args.name:
        harness = project.get_harness(args.name)
        if not harness.exists():
            print(f'[{Colors.RED}-{Colors.END}] Harness "{args.name}" not found')
            return
        
        harness_file = harness.harness_config()
        if not harness_file.exists():
            print(f'[{Colors.RED}-{Colors.END}] Harness file not found for "{args.name}"')
            return
        
        try:
            with open(harness_file, 'r') as f:
                harness_data = json.load(f)
            
            events = harness_data.get('events', [])
            
            print(f'[{Colors.GREEN}+{Colors.END}] Harness: {Colors.BOLD}{Colors.CYAN}{args.name}{Colors.END}')
            print(f'  {Colors.CYAN}*{Colors.END} Crashes: {Colors.CYAN}{harness.num_crashes()}{Colors.END}')
            print(f'  {Colors.CYAN}*{Colors.END} Crash Types: {Colors.CYAN}{harness.num_crash_types()}{Colors.END}')
            
            cost_per_task = harness.cost_per_task()
            if len(cost_per_task) > 0:
                total_cost = sum(cost_per_task.values())
                print(f'  {Colors.CYAN}*{Colors.END} Total Cost: {Colors.YELLOW}${total_cost:.2f}{Colors.END}')
            
            print()
            
            # Show events summary first
            display_events_summary(events, failed_only=args.failed_only)
            
            # Show detailed events
            display_events(events, failed_only=args.failed_only, harness_data=harness_data, show_prompts=args.show_prompts)
            
        except Exception as e:
            print(f'[{Colors.RED}-{Colors.END}] Error reading harness file: {e}')
            return
        
        return

    # Default behavior - show project info
    config = project.get_config()
    print(f'[{Colors.GREEN}+{Colors.END}] Config:')
    print(f'  {Colors.CYAN}*{Colors.END} {Colors.BOLD}repo{Colors.END}={Colors.YELLOW}{config.repo}{Colors.END}')
    print(f'  {Colors.CYAN}*{Colors.END} {Colors.BOLD}commit{Colors.END}={Colors.YELLOW}{config.commit}{Colors.END}')
    print(f'  {Colors.CYAN}*{Colors.END} {Colors.BOLD}repro_per_crash{Colors.END}={Colors.YELLOW}{config.repro_per_crash}{Colors.END}')
    print(f'  {Colors.CYAN}*{Colors.END} {Colors.BOLD}llm_min_per_crash{Colors.END}={Colors.YELLOW}{config.llm_min_per_crash}{Colors.END}')

    if project.has_build_config():
        print(f'[{Colors.GREEN}+{Colors.END}] Build configured')
    else:
        print(f'[{Colors.RED}-{Colors.END}] Build not configured (run {Colors.CYAN}agf gen{Colors.END})')

    harnesses = sorted(project.get_harnesses(), key=lambda x: x.path.name)
    if len(harnesses) == 0:
        print(f'[{Colors.RED}-{Colors.END}] No harnesses found (run {Colors.CYAN}agf infer{Colors.END})')
    else:
        print(f'[{Colors.GREEN}+{Colors.END}] Harnesses:')
        for harness in harnesses:
            line = f'  {Colors.CYAN}*{Colors.END} {harness.path.name} ({Colors.CYAN}{harness.num_crashes()}{Colors.END} crashes, {Colors.CYAN}{harness.num_crash_types()}{Colors.END} types)'
            cost_per_task = harness.cost_per_task()
            if len(cost_per_task) > 0:
                total_cost = sum(cost_per_task.values())
                line += f' | {Colors.YELLOW}${total_cost:.2f}{Colors.END}'
            print(line)
        
        print()
        print(f'[{Colors.CYAN}i{Colors.END}] Use {Colors.CYAN}agf info --name <harness_name>{Colors.END} to view synthesis events for a specific harness')
        print(f'[{Colors.CYAN}i{Colors.END}] Use {Colors.CYAN}--failed-only{Colors.END} flag to show only endpoints that were never successfully validated')
        print(f'[{Colors.CYAN}i{Colors.END}] Use {Colors.CYAN}--show-prompts{Colors.END} flag to show detailed prompt templates and LLM responses')

def register(subparsers):
    subparser = subparsers.add_parser('info', help='Display information about the project')
    subparser.add_argument('path', nargs='?', help='The path to the project', default='.')
    subparser.add_argument('--name', type=str, help='Show synthesis events for a specific harness')
    subparser.add_argument('--failed-only', action='store_true', help='Show only endpoints that were never successfully validated')
    subparser.add_argument('--show-prompts', action='store_true', help='Show detailed prompt templates, variables, and LLM responses')
    subparser.set_defaults(func=do_info)
