from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import subprocess
import json
import tempfile
import os
import time
import re
from ..utils.colors import Colors

@dataclass
class CrashResult:
    name: str
    minimized_graph: str
    external_code: str
    crash_report: str

    crash_summary: str

    def pprint(self):
        print(f"\n{Colors.GREEN}Minimized Graph:{Colors.END}\n{self.minimized_graph}")
        print(f"\n{Colors.BLUE}External Code:{Colors.END}\n{self.external_code}")
        print(f"\n{Colors.RED}Crash Report:{Colors.END}\n{self.crash_report}")


def strip_summary(s: str) -> str:
    if not ' in ' in s:
        return s
    pre, post = s.split(' in ')
    # remove anything matching " (.*) " from pre
    pre = re.sub(r' \(([^\)]*)\)', ' (...)', pre)
    return pre + ' in ' + post


def try_generate_reproducer(tmp_path: Path, fuzzer: str, ext_build: str, crash_file: Path) -> Optional[CrashResult]:
    """Attempts to generate a reproducer for a crash file.
    Returns CrashResult if successful, None otherwise."""
    
    # Step 1: Minimize the crash
    minimized_file = tmp_path / "minimized"
    minimize_cmd = [f"./{fuzzer}", "-m", str(crash_file), "--minimize-output", str(minimized_file)]
    try:
        subprocess.run(minimize_cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        # print(f"Failed to minimize crash: {e}")
        return None
        
    if not minimized_file.exists():
        # print("Minimization failed - no output file produced")
        return None
        
    # Step 2: Generate external reproducer
    external_file = tmp_path / "reproducer.cpp"
    write_cmd = ["agf2", "write", fuzzer, str(minimized_file), "-o", str(external_file)]
    try:
        subprocess.run(write_cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        # print(f"Failed to generate external reproducer: {e}")
        return None
        
    if not external_file.exists():
        # print("External reproducer generation failed - no output file produced")
        return None
        
    # Step 3: Build the external reproducer
    external_base = external_file.with_suffix("")  # Remove .cpp extension
    try:
        subprocess.run([ext_build, str(external_base)], check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        print(f"Failed to build external reproducer: {e}")
        print(e.stderr.decode())
        return None

    # Run the binary and try to extract ASAN report
    asan_report = ''
    env = os.environ.copy()
    env["ASAN_OPTIONS"] = f"log_path={tmp_path / 'asan_log.txt'}"
    for _ in range(25):
        try:
            r = subprocess.run([
                str(tmp_path / "reproducer")],
                capture_output=True,
                env=env,
                timeout=1
            )
            if r.returncode == 1:
                # ASAN detected a crash
                # find the file matching the pattern "asan_log.txt.*"
                log_path = next(tmp_path.glob("asan_log.txt.*"))
                asan_report = log_path.read_text()
                break
        except subprocess.CalledProcessError as e:
            print(f"Error running external reproducer: {e}")

    if asan_report == '':
        print("Failed to reproduce crash with external reproducer")
        return None
    
    crash_summary = asan_report.split("SUMMARY: ")[1].split("\n")[0].strip()
    crash_summary = strip_summary(crash_summary)

    return CrashResult(
        name=crash_file.name,
        minimized_graph=minimized_file.read_text(),
        external_code=external_file.read_text(),
        crash_report=asan_report,
        crash_summary=crash_summary
    )
