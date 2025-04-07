from pathlib import Path
from typing import Optional
import subprocess
import os
import tempfile

def check_queue_safe(tmp_path: Path, fuzzer: str, ext_build: str, queue_file: Path) -> bool:
    """Attempts to verify if a queue file is safe to run.
    Returns True if safe (no crashes), False if crashes."""

    # Step 1: Generate external reproducer
    external_file = tmp_path / "reproducer.cpp"
    write_cmd = ["agf2", "write", fuzzer, str(queue_file), "-o", str(external_file)]
    try:
        subprocess.run(write_cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        print(f"Failed to generate external reproducer: {e}")
        return False

    if not external_file.exists():
        print("External reproducer generation failed - no output file produced")
        return False

    # Step 2: Build the external reproducer
    external_base = external_file.with_suffix("")  # Remove .cpp extension
    try:
        subprocess.run([ext_build, str(external_base)], check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        print(f"Failed to build external reproducer: {e}")
        print(e.stderr.decode())
        return False

    # Run the binary multiple times to check for crashes
    env = os.environ.copy()
    env["ASAN_OPTIONS"] = f"log_path={tmp_path / 'asan_log.txt'}"

    # Try running it 25 times to be more confident it's safe
    for _ in range(25):
        try:
            r = subprocess.run([str(tmp_path / "reproducer")],
                             capture_output=True,
                             env=env,
                             timeout=1
                            )
            # Check if any ASAN logs were created
            if list(tmp_path.glob("asan_log.txt.*")):
                return False

        except Exception as e:
            return False

    return True
