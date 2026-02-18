"""
Analyze crashes from a folder without running the fuzzer.

This command processes crash files through the distillation pipeline:
1. Minimize each crash internally using the fuzzer
2. Convert to external C++ repro  
3. Run through the distillation agent
4. Export assessments to an output folder
5. Track crash history for duplicate detection
"""

from argparse import ArgumentParser
from pathlib import Path
import hashlib
import json
import tempfile
import subprocess
from typing import Optional
from pydantic import BaseModel

from tqdm.auto import tqdm

from ..utils.colors import Colors
from ..utils.crash_repro import try_generate_reproducer
from .distill_agent import run_distill_agent, CrashAssessment
from .data import ProjectMetadata


class CrashInfo(BaseModel):
    """Information about a processed crash."""
    bucket_hash: str
    summary: str
    original_testcase: str
    harness_revision: int = 0  # Always 0 for analyze-crashes (no revisions)


def bucket_hash(summary: str) -> str:
    """Generate a hash for crash bucketing based on the summary."""
    return hashlib.sha256(summary.strip().encode()).hexdigest()[:8]


def _truncate(s: Optional[str], limit: int) -> Optional[str]:
    """Truncate a string to a maximum length."""
    if s is None:
        return None
    try:
        s = str(s)
    except Exception:
        return None
    if len(s) <= limit:
        return s
    return s[:limit] + f"\n... (truncated, {len(s)} chars total)"


def _build_history_entry(crash_info: CrashInfo, assessment_obj: dict) -> dict:
    """
    Build a history object for the distill-agent.
    
    We intentionally include rich context (structured triage + short text
    fields) so the agent can recognize duplicates and reuse prior analysis.
    """
    entry: dict = {
        "bug_hash": crash_info.bucket_hash,
        "summary": crash_info.summary,
        "harness_revision": crash_info.harness_revision,
    }

    # Common optional fields across versions.
    if isinstance(assessment_obj.get("title"), str):
        entry["title"] = assessment_obj.get("title")
    if isinstance(assessment_obj.get("relevant_functions"), list):
        entry["relevant_functions"] = assessment_obj.get("relevant_functions")
    if assessment_obj.get("duplicate_of") is not None:
        entry["duplicate_of"] = assessment_obj.get("duplicate_of")
    if assessment_obj.get("duplicate_reason") is not None:
        entry["duplicate_reason"] = assessment_obj.get("duplicate_reason")
    if assessment_obj.get("can_patch_fuzzer") is not None:
        entry["can_patch_fuzzer"] = assessment_obj.get("can_patch_fuzzer")

    # New schema: structured triage replaces crash_type.
    if isinstance(assessment_obj.get("triage"), dict):
        entry["triage"] = assessment_obj.get("triage")

    # Old schema compatibility (some campaigns may still have these).
    if assessment_obj.get("crash_type") is not None:
        entry["crash_type"] = assessment_obj.get("crash_type")
    if assessment_obj.get("crash_type_reasoning") is not None:
        entry["crash_type_reasoning"] = assessment_obj.get("crash_type_reasoning")

    # Rich-but-bounded text context (kept as excerpts to avoid exploding tokens).
    entry["explanation_excerpt"] = _truncate(assessment_obj.get("explanation"), 3000)
    entry["patch_guidance_excerpt"] = _truncate(assessment_obj.get("patch_guidance"), 2000)
    entry["minimized_testcase_excerpt"] = _truncate(assessment_obj.get("minimized_testcase"), 1200)
    return entry


def compile_harness(workdir: Path):
    """Build the fuzzer from harness.json."""
    print(f'{Colors.GREEN}[+]{Colors.END} Converting harness: {workdir}')
    subprocess.run([
        'stitchi', 'inference', 'to-schema',
        '--meta', str(workdir / 'harness.json'),
        '--output', str(workdir / 'fuzzer.json')
    ], check=True)

    print(f'{Colors.GREEN}[+]{Colors.END} Building fuzzer: {workdir}')
    subprocess.run(['stitchi', 'build', 'full', str(workdir / 'fuzzer')], check=True)


def main(args):
    """Main entry point for the analyze-crashes command."""
    harness_json = Path(args.harness).absolute()
    crashes_dir = Path(args.crashes).absolute()
    output_dir = Path(args.output).absolute()

    if not harness_json.exists():
        print(f'[{Colors.RED}-{Colors.END}] Harness config not found: {harness_json}')
        return

    if not crashes_dir.exists() or not crashes_dir.is_dir():
        print(f'[{Colors.RED}-{Colors.END}] Crashes directory does not exist: {crashes_dir}')
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    skipped_dir = output_dir / 'skipped'
    skipped_dir.mkdir(parents=True, exist_ok=True)

    print(f'[{Colors.GREEN}+{Colors.END}] Harness: {harness_json}')
    print(f'[{Colors.GREEN}+{Colors.END}] Crashes: {crashes_dir}')
    print(f'[{Colors.GREEN}+{Colors.END}] Output: {output_dir}')

    # Build the fuzzer
    print(f'[{Colors.GREEN}+{Colors.END}] Building fuzzer...')
    workdir = harness_json.parent
    
    try:
        compile_harness(workdir)
    except subprocess.CalledProcessError as e:
        print(f'[{Colors.RED}-{Colors.END}] Failed to build fuzzer')
        return

    fuzzer_bin = workdir / 'fuzzer'
    if not fuzzer_bin.exists():
        print(f'[{Colors.RED}-{Colors.END}] Fuzzer binary not found after build')
        return

    print(f'[{Colors.GREEN}+{Colors.END}] Fuzzer built successfully')

    # Create pspec cache
    subprocess.run([str(fuzzer_bin), '--bypass-validation'], capture_output=True)

    # Load the harness metadata for the distill agent
    harness_metadata = ProjectMetadata.model_validate_json(harness_json.read_text())

    # Load existing crash history from output directory
    known_crashes: dict[str, CrashInfo] = {}
    prior_crash_history: list[dict] = []

    for report_dir in output_dir.iterdir():
        if report_dir.is_dir() and report_dir.name != 'skipped':
            info_path = report_dir / 'info.json'
            if not info_path.exists():
                continue
            try:
                crash_info = CrashInfo.model_validate_json(info_path.read_text())
                known_crashes[crash_info.bucket_hash] = crash_info

                # If an assessment exists, include it in history
                assessment_path = report_dir / 'assessment.json'
                if assessment_path.exists():
                    try:
                        assessment_obj = json.loads(assessment_path.read_text())
                        if isinstance(assessment_obj, dict):
                            prior_crash_history.append(_build_history_entry(crash_info, assessment_obj))
                    except Exception:
                        continue
            except Exception:
                continue

    print(f'[{Colors.GREEN}+{Colors.END}] Loaded {len(known_crashes)} existing crash reports')

    # Get list of crash files to process
    crash_files = [
        f for f in crashes_dir.iterdir()
        if f.is_file() and not f.name.startswith('.')
    ]

    if len(crash_files) == 0:
        print(f'[{Colors.YELLOW}*{Colors.END}] No crash files found in {crashes_dir}')
        return

    print(f'[{Colors.GREEN}+{Colors.END}] Found {len(crash_files)} crash files to process')

    # Track which files we've already processed (by name)
    processed_files = set()
    for report_dir in output_dir.iterdir():
        if report_dir.is_dir() and report_dir.name != 'skipped':
            info_path = report_dir / 'info.json'
            if info_path.exists():
                try:
                    crash_info = CrashInfo.model_validate_json(info_path.read_text())
                    processed_files.add(Path(crash_info.original_testcase).name)
                except Exception:
                    pass

    # Also check skipped files
    for skipped_file in skipped_dir.iterdir():
        if skipped_file.is_file():
            # Remove .reason suffix to get original crash name
            processed_files.add(skipped_file.stem)

    # Filter to unprocessed files
    crash_files = [f for f in crash_files if f.name not in processed_files]

    if len(crash_files) == 0:
        print(f'[{Colors.GREEN}+{Colors.END}] All crash files have already been processed')
        return

    print(f'[{Colors.GREEN}+{Colors.END}] {len(crash_files)} crashes remaining to process')

    # Process each crash file
    for crash_file in tqdm(crash_files, desc='Processing crashes'):
        print(f'\n[{Colors.CYAN}*{Colors.END}] Processing: {crash_file.name}')

        with tempfile.TemporaryDirectory() as crash_tmpdir:
            crash_tmpdir = Path(crash_tmpdir)

            # Try to generate a reproducer (minimize + convert to external)
            repro = try_generate_reproducer(crash_tmpdir, str(fuzzer_bin), crash_file)

            if repro is None:
                print(f'[{Colors.RED}-{Colors.END}] Failed to generate reproducer for {crash_file.name}')
                (skipped_dir / f'{crash_file.name}.reason').write_text('Failed to generate reproducer')
                continue

            # Compute bucket hash from crash summary
            bhash = bucket_hash(repro.crash_summary)

            # Create crash info
            crash_info = CrashInfo(
                bucket_hash=bhash,
                summary=repro.crash_summary,
                original_testcase=str(crash_file.absolute()),
                harness_revision=0
            )

            # Check if this is a known crash bucket
            is_new_bucket = bhash not in known_crashes
            if is_new_bucket:
                print(f'[{Colors.GREEN}NEW{Colors.END}] {crash_file.name} :: {repro.crash_summary}')
            else:
                print(f'[{Colors.YELLOW}DUP{Colors.END}] {crash_file.name} :: {repro.crash_summary}')

            # Create output directory for this crash
            crash_output_dir = output_dir / bhash
            crash_output_dir.mkdir(parents=True, exist_ok=True)

            # Save crash info
            (crash_output_dir / 'info.json').write_text(crash_info.model_dump_json(indent=2))

            # Save the external reproducer
            (crash_output_dir / 'reproducer.cpp').write_text(repro.external_code)

            # Run the distillation agent
            print(f'[{Colors.CYAN}*{Colors.END}] Running distillation agent...')
            try:
                assessment = run_distill_agent(
                    harness_metadata,
                    repro.external_code,
                    history=prior_crash_history if prior_crash_history else None
                )

                # Save the assessment
                assessment_path = crash_output_dir / 'assessment.json'
                assessment_path.write_text(assessment.model_dump_json(indent=2))

                print(f'[{Colors.GREEN}+{Colors.END}] Assessment saved: {assessment.title}')

                # Update crash history for subsequent crashes
                known_crashes[bhash] = crash_info
                prior_crash_history.append(_build_history_entry(crash_info, assessment.model_dump()))

                # Print duplicate info if applicable
                if assessment.duplicate_of:
                    print(f'[{Colors.YELLOW}*{Colors.END}] Duplicate of: {assessment.duplicate_of}')
                    if assessment.duplicate_reason:
                        print(f'    Reason: {assessment.duplicate_reason}')

            except Exception as e:
                print(f'[{Colors.RED}-{Colors.END}] Distillation agent failed: {e}')
                (skipped_dir / f'{crash_file.name}.reason').write_text(f'Distillation agent failed: {e}')
                continue

    print(f'\n[{Colors.GREEN}+{Colors.END}] Analysis complete!')
    print(f'[{Colors.GREEN}+{Colors.END}] Total unique crashes: {len(known_crashes)}')
    print(f'[{Colors.GREEN}+{Colors.END}] Results saved to: {output_dir}')


def register(subparsers: ArgumentParser):
    """Register the analyze-crashes command."""
    parser = subparsers.add_parser(
        'analyze-crashes',
        help='Analyze crash files through the distillation pipeline'
    )
    parser.add_argument(
        '--harness',
        type=str,
        required=True,
        help='Path to the harness.json file'
    )
    parser.add_argument(
        '--crashes',
        type=str,
        required=True,
        help='Path to the folder containing crash files'
    )
    parser.add_argument(
        '--output',
        type=str,
        required=True,
        help='Output directory for assessments'
    )
    parser.set_defaults(func=main)
