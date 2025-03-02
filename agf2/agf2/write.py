import argparse
import subprocess
import re
from pathlib import Path
from typing import Optional


def extract_cpp_code(stdout: str) -> Optional[str]:
    """Extract C++ code between -----BEGIN GRAPH WRITE----- and -----END GRAPH WRITE-----"""
    pattern = r'-----BEGIN GRAPH WRITE-----\n(.*?)-----END GRAPH WRITE-----'
    match = re.search(pattern, stdout, re.DOTALL)
    if match:
        return match.group(1).strip()
    return None


def write_graph(fuzzer: str, testcase: str, output: Optional[str] = None) -> Optional[str]:
    """Run fuzzer with write mode on testcase and extract the C++ code.
    
    Args:
        fuzzer: Path to the fuzzer binary
        testcase: Path to the testcase file
        output: Optional path to write the extracted code to
        
    Returns:
        The extracted C++ code if successful, None otherwise
    """
    try:
        # Run the fuzzer in write mode
        result = subprocess.run(
            [fuzzer, '-w', testcase],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        if result.returncode != 0:
            print(f"Error running fuzzer: {result.stderr}")
            return None
            
        # Extract the C++ code
        cpp_code = extract_cpp_code(result.stdout)
        if not cpp_code:
            print("No C++ code found in fuzzer output")
            return None
            
        # Write to file if output path provided
        if output:
            with open(output, 'w') as f:
                f.write(cpp_code)
            print(f"Wrote C++ code to {output}")
        else:
            print(cpp_code)
            
        return cpp_code
        
    except Exception as e:
        print(f"Error: {str(e)}")
        return None


def main(args):
    write_graph(args.fuzzer, args.testcase, args.output)


def register(subparsers):
    parser = subparsers.add_parser('write', help='Extract C++ code from fuzzer write mode')
    parser.add_argument('fuzzer', help='Path to the fuzzer binary')
    parser.add_argument('testcase', help='Path to the testcase file')
    parser.add_argument('-o', '--output', help='Optional path to write the extracted code to')
    parser.set_defaults(func=main)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    register(subparsers)
    args = parser.parse_args()
    if hasattr(args, 'func'):
        args.func(args)
    else:
        parser.print_help() 