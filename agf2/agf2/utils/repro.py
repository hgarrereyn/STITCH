import tempfile
import subprocess
from pathlib import Path


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
        run_result = subprocess.run(
            ['casr-san', '--stdout', '--', binary_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        print(f'>> run_result: {run_result.returncode} {run_result.stdout}')
        
        if run_result.returncode == 0:
            return f"Program crashed:\n{run_result.stdout}"
        else:
            return f"Program ran successfully"
