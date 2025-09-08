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
import argparse


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

    # convert /tmp/tmpwwe8s9nb/reproducer to <tmpdir>/reproducer
    pre = re.sub(r'/tmp/tmp[a-z0-9]+/reproducer', '<tmpdir>/reproducer', pre)

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
    
    # Choose which report/summary pair to use
    if loc_gdb is None:
        report_json = json.dumps(report_san, indent=2)
        summary = f'{typ_san} in {loc_san}'
    elif loc_san is None:
        report_json = json.dumps(report_gdb, indent=2)
        summary = f'{typ_gdb} in {loc_gdb}'
    else:
        # We have both reports, use gdb_loc and san type unless type is AccessViolation
        if typ_san != 'AccessViolation':
            report_json = json.dumps(report_san, indent=2)
            summary = f'{typ_san} in {loc_gdb}'
        else:
            report_json = json.dumps(report_gdb, indent=2)
            summary = f'{typ_gdb} in {loc_gdb}'

    # ------- Assertion message refinement -------
    # The previous logic may classify assertion failures simply as "AbortSignal".
    # Try to detect assertion messages and create a more informative summary.
 
    # If the type looks like an abort, attempt to extract assertion details
    if summary.startswith('AbortSignal') or summary.startswith('SIGABRT') or summary.startswith('Abort'):
        assert_msg = _extract_assertion_message(cmd)
        if assert_msg:
            # Preserve the location part if we have one
            if ' in ' in summary:
                loc_part = summary.split(' in ', 1)[1]
                summary = f'Assertion "{assert_msg}" in {loc_part}'
            else:
                summary = f'Assertion "{assert_msg}"'

    return report_json, strip_summary(summary)


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
        print(f"Failed to minimize crash: {e}")
        return None
        
    if not minimized_file.exists():
        print("Minimization failed - no output file produced")
        return None
        
    # Step 2: Generate external reproducer
    external_file = tmp_path / 'reproducer.cpp'
    write_cmd = ['agfi', 'write', fuzzer, str(minimized_file), '-o', str(external_file)]
    try:
        subprocess.run(write_cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        print(f"Failed to generate external reproducer: {e}")
        return None
        
    if not external_file.exists():
        print("External reproducer generation failed - no output file produced")
        return None
        
    # Step 3: Build the external reproducer
    external_base = external_file.with_suffix("")  # Remove .cpp extension
    try:
        subprocess.run([
            'agfi', 'build', 'external', str(external_base)
        ], check=True, capture_output=False)
    except subprocess.CalledProcessError as e:
        print(f"Failed to build external reproducer: {e}")
        code = external_file.read_text()
        print('-' * 80)
        print(code)
        print('-' * 80)
        return None

    # Run the binary and try to extract ASAN report
    crash_report, crash_summary = observe_crash([str((tmp_path / 'reproducer').absolute())])

    if crash_report is None:
        print("Failed to observe crash with external reproducer")
        return None

    return ReproducedCrash(
        name=crash_file.name,
        minimized_graph=minimized_file.read_text(),
        external_code=external_file.read_text(),
        crash_report=crash_report,
        crash_summary=crash_summary
    )


def register(subparsers):
    crash_parser = subparsers.add_parser('crash-summary')
    crash_parser.add_argument('cmd', nargs=argparse.REMAINDER, help='Command to execute')
    crash_parser.set_defaults(func=run_crash_repro)


def run_crash_repro(args):
    cmd = args.cmd
    if not cmd:
        print('No command provided to crash-repro')
        return
    crash_report, crash_summary = observe_crash(cmd)
    print(crash_summary)


def _extract_assertion_message(cmd: List[str]) -> Optional[str]:
    """Run the given command, capture stderr once and try to extract the text of an
    assertion message (e.g. "foo != bar") if the program terminates via an
    assertion failure. Returns the extracted message if found, otherwise None.
    This is a best-effort heuristic that looks for common assertion patterns.
    It should never raise – on any failure it falls back to returning None."""
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=5)
        stderr = r.stderr.decode(errors="ignore")
        # Common patterns that appear in libc assert as well as custom asserts.
        patterns = [
            r"Assertion `([^`]+)` failed",  # glibc / clang assert w/ back-quotes
            r"Assertion '([^']+)' failed",  # single quotes
            r"Assertion `([^']+)' failed",  # mismatched quotes
            r'Assertion "([^"]+)" failed',  # double quotes
            r"assertion failed: ([^\n\r]+)",  # rust / go style
            r"failed assertion `([^`]+)`",  # misc
        ]
        for pat in patterns:
            m = re.search(pat, stderr)
            if m:
                return m.group(1).strip()
    except Exception:
        pass
    return None
