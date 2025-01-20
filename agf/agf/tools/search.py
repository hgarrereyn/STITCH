#!/usr/bin/env python3

import argparse
import os
import re
from typing import List, Tuple

def find_function_invocations(codebase_path: str, function_name: str, context_lines: int = 3) -> List[Tuple[str, int, List[str]]]:
    """
    Search for function invocations in the codebase and return their context.
    
    Args:
        codebase_path: Path to the codebase directory
        function_name: Name of the function to search for
        context_lines: Number of lines of context before and after the match
        
    Returns:
        List of tuples containing (filename, line number, context lines)
    """
    results = []
    
    # Create regex pattern for function invocation
    # This handles cases like function(), obj.function(), etc.
    pattern = rf'(?:^|\W)({function_name})\s*\('
    
    for root, _, files in os.walk(codebase_path):
        for file in files:
            # Skip common non-code files and directories
            if file.startswith('.') or any(file.endswith(ext) for ext in ['.pyc', '.git']):
                continue
                
            filepath = os.path.join(root, file)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                    
                for i, line in enumerate(lines):
                    if re.search(pattern, line):
                        # Calculate context range
                        start = max(0, i - context_lines)
                        end = min(len(lines), i + context_lines + 1)
                        
                        # Get context lines
                        context = lines[start:end]
                        
                        # Store result with relative path
                        rel_path = os.path.relpath(filepath, codebase_path)
                        results.append((rel_path, i + 1, context))
            except (UnicodeDecodeError, IOError):
                # Skip files that can't be read as text
                continue
                
    return results

def main():
    parser = argparse.ArgumentParser(description='Find function invocations in a codebase with context')
    parser.add_argument('codebase_path', help='Path to the codebase directory')
    parser.add_argument('function_name', help='Name of the function to search for')
    parser.add_argument('-c', '--context', type=int, default=3,
                        help='Number of context lines before and after (default: 3)')
    parser.add_argument('-s', '--sample', type=int,
                        help='Randomly sample this many matches (if not provided, show all matches)')
    
    args = parser.parse_args()
    
    results = find_function_invocations(args.codebase_path, args.function_name, args.context)
    
    if not results:
        print(f"No invocations of '{args.function_name}' found.")
        return

    # If sampling is requested and we have more results than the sample size
    if args.sample is not None and len(results) > args.sample:
        import random
        results = random.sample(results, args.sample)
        print(f"\nShowing {args.sample} random matches out of {len(results)} total matches found:")
    else:
        print(f"\nShowing all {len(results)} matches found:")
        
    # Print results with nice formatting
    for filename, line_num, context in results:
        print(f"\n{filename}:{line_num}")
        print("-" * 60)
        
        for i, ctx_line in enumerate(context):
            # Calculate the actual line number for this context line
            ctx_line_num = line_num - (len(context) // 2) + i
            prefix = f"{ctx_line_num:4d} | "
            
            # Highlight the matching line
            if ctx_line_num == line_num:
                print(f"{prefix}>>> {ctx_line.rstrip()}")
            else:
                print(f"{prefix}    {ctx_line.rstrip()}")
        print("-" * 60)

if __name__ == "__main__":
    main()
