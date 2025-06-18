from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Tuple
import subprocess
import json
import tempfile
import os
import time
import re
from pydantic import BaseModel


class CrashResult(BaseModel):
    filepath: Path
    name: str
    crash_report: str
    crash_summary: str


class ReproducedCrash(BaseModel):
    name: str
    minimized_graph: str
    external_code: str
    crash_report: str
    crash_summary: str


def strip_summary(s: str) -> str:
    if not ' in ' in s:
        return s
    pre, post = s.split(' in ')
    # remove anything matching " (.*) " from pre
    pre = re.sub(r' \(([^\)]*)\)', ' (...)', pre)
    return pre + ' in ' + post


def run_casr_san(cmd: List[str]) -> Optional[dict]:
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            report = Path(tmpdir) / 'casr-san.json'
            r = subprocess.run(
                ['casr-san', '--output', report, '--', *cmd],
                capture_output=True,
                cwd=tmpdir,
                timeout=5
            )
            if r.returncode == 0:
                return json.load(open(report))
            return None
    except Exception as e:
        return None

def run_casr_gdb(cmd: List[str]) -> Optional[dict]:
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            report = Path(tmpdir) / 'casr-gdb.txt'
            r = subprocess.run(
                ['casr-gdb', '--output', report, '--', *cmd],
                capture_output=True,
                cwd=tmpdir,
                timeout=5
            )
            if r.returncode == 0:
                return json.load(open(report))
            return None
    except Exception as e:
        return None


def observe_crash(cmd: List[str]) -> Optional[Tuple[str, str]]:
    """Runs a command and returns the crash report and summary if a crash is observed.
    Returns None otherwise."""
    
    typ_gdb = None
    loc_gdb = None
    report_gdb = run_casr_gdb(cmd)
    if report_gdb is not None:
        loc_gdb = report_gdb['CrashLine']
        typ_gdb = report_gdb['CrashSeverity']['ShortDescription']
    
    typ_san = None
    loc_san = None
    report_san = run_casr_san(cmd)
    if report_san is not None:
        loc_san = report_san['CrashLine']
        typ_san = report_san['CrashSeverity']['ShortDescription']

    if loc_gdb is None and loc_san is None:
        return None, None
    
    if loc_gdb is None:
        return json.dumps(report_san), f'{typ_san} in {loc_san}'
    elif loc_san is None:
        return json.dumps(report_gdb), f'{typ_gdb} in {loc_gdb}'
    else:
        # We have both reports, use gdb_loc and san type unless type is AccessViolation
        if typ_san != 'AccessViolation':
            return json.dumps(report_san), f'{typ_san} in {loc_gdb}'
        else:
            return json.dumps(report_gdb), f'{typ_gdb} in {loc_gdb}'


def build_and_observe_crash(testcase: str) -> Optional[Tuple[str, str]]:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        external_file_path = tmpdir / 'reproducer.cpp'
        with open(external_file_path, 'w') as f:
            f.write(testcase)

        try:
            subprocess.run(['agfi', 'build', 'external', str(tmpdir / 'reproducer')], check=True, capture_output=False)
        except subprocess.CalledProcessError as e:
            return None, None
        
        return observe_crash([str(tmpdir / 'reproducer')])


def try_get_crash_result(fuzzer: str, crash_file: Path) -> Optional[CrashResult]:
    """Attempts to observe a fuzzer crash.
    Returns CrashResult if successful, None otherwise."""
    cmd = [fuzzer, '-e', str(crash_file)]
    crash_report, crash_summary = observe_crash(cmd)
    if crash_report is None:
        return None

    return CrashResult(
        filepath=crash_file,
        name=crash_file.name,
        crash_report=crash_report,
        crash_summary=crash_summary
    )


def try_generate_reproducer(tmp_path: Path, fuzzer: str, crash_file: Path) -> Optional[ReproducedCrash]:
    """Attempts to generate a reproducer for a crash file.
    Returns ReproducedCrash if successful, None otherwise."""
    
    # Step 1: Minimize the crash
    minimized_file = tmp_path / 'minimized'
    minimize_cmd = [fuzzer, '-m', str(crash_file), '--minimize-output', str(minimized_file)]
    try:
        subprocess.run(minimize_cmd, check=True, capture_output=True, timeout=30)
    except Exception as e:
        # print(f"Failed to minimize crash: {e}")
        return None
        
    if not minimized_file.exists():
        # print("Minimization failed - no output file produced")
        return None
        
    # Step 2: Generate external reproducer
    external_file = tmp_path / 'reproducer.cpp'
    write_cmd = ['agfi', 'write', fuzzer, str(minimized_file), '-o', str(external_file)]
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
        subprocess.run([
            'agfi', 'build', 'external', str(external_base)
        ], check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        # print(f"Failed to build external reproducer: {e}")
        return None

    # Run the binary and try to extract ASAN report
    crash_report, crash_summary = observe_crash([str((tmp_path / 'reproducer').absolute())])

    if crash_report is None:
        # print("Failed to observe crash with external reproducer")
        return None

    return ReproducedCrash(
        name=crash_file.name,
        minimized_graph=minimized_file.read_text(),
        external_code=external_file.read_text(),
        crash_report=crash_report,
        crash_summary=crash_summary
    )
