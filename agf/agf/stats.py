from pathlib import Path
import json
from typing import Dict, List, Set, Tuple
from collections import defaultdict, deque

from .utils.colors import Colors
from .utils.project import Project, Harness


def do_stats(args):
    """Analyze harness statistics including constructability analysis."""
    path = Path(args.path)
    project = Project(path)
    if not project.exists() or not project.has_config():
        print(f'[{Colors.RED}-{Colors.END}] Could not find project at {path.absolute()}')
        return

    harness_name = args.harness
    if not harness_name:
        print(f'[{Colors.RED}-{Colors.END}] Harness name is required. Use --harness <name>')
        return

    harness = project.get_harness(harness_name)
    if not harness.exists():
        print(f'[{Colors.RED}-{Colors.END}] Harness "{harness_name}" not found')
        return

    harness_file = harness.harness_config()
    if not harness_file.exists():
        print(f'[{Colors.RED}-{Colors.END}] Harness file not found for "{harness_name}"')
        return

    try:
        with open(harness_file, 'r') as f:
            harness_data = json.load(f)
        
        print(f'[{Colors.GREEN}+{Colors.END}] Harness Statistics: {Colors.BOLD}{Colors.CYAN}{harness_name}{Colors.END}')
        print()
        
        # Basic statistics
        display_basic_stats(harness_data)
        
        # Constructability analysis
        max_depth = getattr(args, 'depth', 6) or 6
        display_constructability_analysis(harness_data, max_depth)
        
    except Exception as e:
        print(f'[{Colors.RED}-{Colors.END}] Error reading harness file: {e}')
        return


def display_basic_stats(harness_data: dict):
    """Display basic statistics about objects and endpoints."""
    objects = harness_data.get('objects', [])
    validated_endpoints = harness_data.get('endpoints', [])
    
    print(f'[{Colors.CYAN}*{Colors.END}] Objects: {Colors.CYAN}{len(objects)}{Colors.END}')
    print(f'[{Colors.CYAN}*{Colors.END}] Validated Endpoints: {Colors.GREEN}{len(validated_endpoints)}{Colors.END}')
    print()


def display_constructability_analysis(harness_data: dict, max_depth: int):
    """Analyze which endpoints are unconstructable due to missing dependencies."""
    objects = harness_data.get('objects', []) or []
    validated_endpoints = harness_data.get('endpoints', []) or []

    # Create object type mapping (ObjectStub uses 'type'; fallback to 'name')
    object_names: List[str] = []
    for obj in objects:
        if isinstance(obj, dict):
            name = obj.get('type') or obj.get('name') or ''
        else:
            name = str(obj)
        if name:
            object_names.append(name)
    object_types = {name: i for i, name in enumerate(object_names)}

    # Build dependency graph using only validated endpoints (enabled)
    constructable_endpoints, unconstructable_endpoints, missing_constructors = analyze_constructability(
        validated_endpoints, object_types, max_depth
    )
    
    print(f'[{Colors.CYAN}*{Colors.END}] Constructability Analysis:')
    print(f'  {Colors.GREEN}Constructable Endpoints: {len(constructable_endpoints)}{Colors.END}')
    print(f'  {Colors.RED}Unconstructable Endpoints: {len(unconstructable_endpoints)}{Colors.END}')
    print()
    
    if unconstructable_endpoints:
        print(f'[{Colors.RED}Unconstructable Endpoints:{Colors.END}]')
        for endpoint_name, reason in unconstructable_endpoints:
            print(f'  {Colors.RED}•{Colors.END} {endpoint_name}: {reason}')
        print()

    if missing_constructors:
        print(f'[{Colors.YELLOW}!{Colors.END}] Types without constructors (depth={max_depth}):')
        for t in sorted(missing_constructors):
            print(f'  - {t}')
        print()


def analyze_constructability(validated_endpoints: List[dict], 
                           object_types: Dict[str, int],
                           max_depth: int) -> Tuple[List[str], List[Tuple[str, str]], Set[str]]:
    """
    Analyze which endpoints are constructable based on input/output dependencies,
    mirroring gf_core's recursive depth-limited approach for constructors.

    Returns:
        (constructable_endpoints, unconstructable_endpoints, missing_constructors)
    """
    # Normalize endpoints: endpoint_name -> (input_types_names, output_types_names)
    endpoint_inputs: Dict[str, List[str]] = {}
    endpoint_outputs: Dict[str, List[str]] = {}
    types_seen: Set[str] = set()

    for endpoint in validated_endpoints:
        name = endpoint.get('name', '')
        inputs = endpoint.get('inputs', []) or []
        outputs = endpoint.get('outputs', []) or []

        in_types: List[str] = []
        for inp in inputs:
            if isinstance(inp, dict):
                tname = inp.get('type') or inp.get('name') or ''
            else:
                tname = str(inp)
            if tname:
                in_types.append(tname)
                types_seen.add(tname)

        out_types: List[str] = []
        for out in outputs:
            if isinstance(out, dict):
                tname = out.get('type') or out.get('name') or ''
            else:
                tname = str(out)
            if tname:
                out_types.append(tname)
                types_seen.add(tname)

        endpoint_inputs[name] = in_types
        endpoint_outputs[name] = out_types

    # Build producers map: type_name -> list of endpoints that produce it
    producers: Dict[str, List[str]] = defaultdict(list)
    for ep_name, outs in endpoint_outputs.items():
        for t in outs:
            producers[t].append(ep_name)

    memo: Dict[Tuple[str, int], bool] = {}
    visiting: Set[Tuple[str, int]] = set()

    def type_constructible(tname: str, depth: int) -> bool:
        if depth == 0:
            return False
        key = (tname, depth)
        if key in memo:
            return memo[key]
        if key in visiting:
            # Cycle detected at this depth; treat as not constructible for safety
            memo[key] = False
            return False
        visiting.add(key)

        ok = False
        for ep_name in producers.get(tname, []):
            # All inputs of the producer must be constructible at depth-1
            inputs_ok = True
            for it in endpoint_inputs.get(ep_name, []):
                if not type_constructible(it, depth - 1):
                    inputs_ok = False
                    break
            if inputs_ok:
                ok = True
                break

        visiting.remove(key)
        memo[key] = ok
        return ok

    # Compute missing constructors (for declared object types and seen types)
    all_types: Set[str] = set(object_types.keys()) | types_seen
    missing_constructors: Set[str] = set()
    for t in all_types:
        if not type_constructible(t, max_depth):
            missing_constructors.add(t)

    # Endpoints that are disabled because they require missing types
    constructable_eps: List[str] = []
    unconstructable_eps: List[Tuple[str, str]] = []

    for ep_name, in_types in endpoint_inputs.items():
        missing = [t for t in in_types if t in missing_constructors]
        if len(missing) == 0:
            constructable_eps.append(ep_name)
        else:
            reason = f"Missing constructors for: {', '.join(sorted(set(missing)))}"
            unconstructable_eps.append((ep_name, reason))

    return constructable_eps, unconstructable_eps, missing_constructors


def register(subparsers):
    """Register the stats subcommand."""
    subparser = subparsers.add_parser('stats', help='Analyze harness statistics and constructability')
    subparser.add_argument('path', nargs='?', help='The path to the project', default='.')
    subparser.add_argument('--harness', required=True, help='The name of the harness to analyze')
    subparser.add_argument('--depth', type=int, default=6, help='Max recursion depth for constructability analysis')
    subparser.set_defaults(func=do_stats)
