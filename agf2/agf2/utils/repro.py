import tempfile
import subprocess
from pathlib import Path

from ..runner.crash_repro import observe_crash


def test_program(code: str, build_script: str) -> str:
    """Compile and run a test case demonstrating the bug. Returns the output of running the program."""
    # Create a temporary directory to store our files
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create the cpp file
        cpp_path = Path(tmpdir) / "test.cpp"
        with open(cpp_path, 'w') as f:
            f.write(code)

        # Get the base path without extension for the build script
        base_path = str(cpp_path.with_suffix(''))
        
        # Run the build script
        build_result = subprocess.run(
            [build_script, base_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        print(f'>> build_result: {build_result.returncode} {build_result.stderr}')
        
        if build_result.returncode != 0:
            return f"Build failed:\n{build_result.stderr}"
        
        # Try to run the program under casr-gdb
        binary_path = base_path  # The build script should create this
        crash_report, crash_summary = observe_crash([binary_path])
        
        print(f'>> crash_report: {crash_report}')
        print(f'>> crash_summary: {crash_summary}')
        
        if crash_report is not None:
            return f"Program crashed:\n{crash_report}"
        else:
            return f"Program ran without crashing"
