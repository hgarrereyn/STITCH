from argparse import ArgumentParser
from pathlib import Path
import subprocess
import os
import time
import hashlib
import tempfile
from pydantic import BaseModel, Field
from typing import Set, List
from tqdm.auto import tqdm
from .utils.crash_repro import observe_crash
from .utils.colors import Colors

# Output directory structure
# - <output_dir>/
#   - .processed/
#     - <crash_name>.status     # crash report
#   - .minimized/
#     - <crash_name>.status     # status of the minimization/repro attempt
#   - <hash>/                   # hash of the crash summary
#     - desc.txt                # crash summary
#     - orig/
#       - <crash_name>.graph    # original testcase graph
#     - repro/
#       - <crash_name>.cpp      # external code

# Max number of minimized external reproducers per crash bucket
MIN_PER_CRASH = 3


# Max number of original testcase graphs stored per crash bucket.
# We keep the first N and stop writing new ones after the limit.
MAX_ORIG_PER_BUCKET = 100


def bucket_hash(summary):
    return hashlib.sha256(summary.strip().encode()).hexdigest()[:8]


def is_same_summary(summary1, summary2):
    if summary1.strip() == summary2.strip():
        return True
    # Sometimes we get slightly different summaries (one includes a column number, the other doesn't)
    # This is a bit hacky, but it works for now
    if summary1.startswith(summary2) or summary2.startswith(summary1):
        return True
    return False


def process_crash(args):
    fuzzer, crash_path, output_dir = args
    report, summary = observe_crash([fuzzer, '-e', crash_path])
    if report is None:
        (output_dir / '.processed' / f'{crash_path.name}.none').touch()
    else:
        (output_dir / '.processed' / f'{crash_path.name}.status').write_text(summary.strip() + '\n')

        hsh = bucket_hash(summary)
        bucket_dir = output_dir / hsh
        if not bucket_dir.exists():
            print(f'[{Colors.GREEN}NEW{Colors.END}] {crash_path.name} :: {summary.strip()}')
            bucket_dir.mkdir(parents=True, exist_ok=True)
        (bucket_dir / 'desc.txt').write_text(summary.strip() + '\n')
        (bucket_dir / 'orig').mkdir(parents=True, exist_ok=True)
        (bucket_dir / 'repro').mkdir(parents=True, exist_ok=True)
        orig_dir = bucket_dir / 'orig'
        target = orig_dir / f'{crash_path.name}.graph'
        try:
            # Allow overwriting an existing file even when at capacity.
            if not target.exists():
                existing = [p for p in orig_dir.iterdir() if p.is_file() and not p.name.startswith('.')]
                if len(existing) >= MAX_ORIG_PER_BUCKET:
                    return
            with open(crash_path, 'rb') as f:
                target.write_bytes(f.read())
        except Exception:
            # Never fail crash processing due to bucket retention bookkeeping.
            return


def run_bucketize(fuzzer, crashes_dir, output_dir, nproc) -> bool:
    """Run each unprocessed crash through the harness and collect the crash summary."""
    processed = set([f.stem for f in (output_dir / '.processed').iterdir() if f.is_file() and not f.name.startswith('.')])
    crashes = set([f.name for f in crashes_dir.iterdir() if f.is_file() and not f.name.startswith('.')])
    new_crashes = list(crashes - processed)
    
    if len(new_crashes) == 0:
        return False

    print(f'[{Colors.GREEN}*{Colors.END}] Processing {len(new_crashes)} crashes')

    for crash in tqdm(new_crashes[:10], desc='Processing crashes'):
        process_crash((fuzzer, crashes_dir / crash, output_dir))
    
    return len(new_crashes) > 10


def minimize_crash(args):
    info, fuzzer, crash_path, output_dir = args
    # Check how many minimized external reproducers are in the bucket
    summary = (output_dir / '.processed' / f'{crash_path.name}.status').read_text().strip()
    bucket_dir = output_dir / bucket_hash(summary)
    if not bucket_dir.exists():
        print(f'[{Colors.RED}FAIL{Colors.END}] {crash_path.name} :: Bucket directory does not exist')
        (output_dir / '.minimized' / f'{crash_path.name}.none').touch()
        return
    minimized = set([f.stem for f in (bucket_dir / 'repro').iterdir() if f.is_file() and not f.name.startswith('.')])
    if len(minimized) >= MIN_PER_CRASH:
        print(f'[{Colors.GREEN}SKIP{Colors.END}] {crash_path.name} :: Already has {len(minimized)} minimized external reproducers')
        (output_dir / '.minimized' / f'{crash_path.name}.skip').touch()
        return

    # Try to minimize the crash
    with tempfile.TemporaryDirectory() as tmp_dir:
        # 1. Run harness minimization
        tmp_dir = Path(tmp_dir)
        out_file = tmp_dir / 'minimized.graph'
        subprocess.run([fuzzer, '-m', str(crash_path), '--minimize-output', str(out_file)], check=True, capture_output=True)
        if not out_file.exists():
            print(f'[{Colors.RED}FAIL{Colors.END}] {crash_path.name} :: Minimization failed')
            (output_dir / '.minimized' / f'{crash_path.name}.fail').touch()
            return
        
        # 2. Convert to external code
        external_file = tmp_dir / 'minimized.cpp'
        subprocess.run(['agfi', 'write', fuzzer, str(out_file), '-o', str(external_file)], check=True, capture_output=True)
        if not external_file.exists():
            print(f'[{Colors.RED}FAIL{Colors.END}] {crash_path.name} :: External code generation failed')
            (output_dir / '.minimized' / f'{crash_path.name}.fail').touch()
            return

        # 3. Build the external code
        subprocess.run([
            'agfi', 'build', 'external',
            '--info', str(info),
            str(tmp_dir / 'minimized')
        ], check=True, capture_output=True)
        if not (tmp_dir / 'minimized').exists():
            print(f'[{Colors.RED}FAIL{Colors.END}] {crash_path.name} :: External code build failed')
            (output_dir / '.minimized' / f'{crash_path.name}.fail').touch()
            return
        
        # 4. Run the external code and observe the crash
        crash_report, crash_summary = observe_crash([str(tmp_dir / 'minimized')])
        if crash_report is None:
            print(f'[{Colors.RED}FAIL{Colors.END}] {crash_path.name} :: Crash report generation failed')
            (output_dir / '.minimized' / f'{crash_path.name}.fail').touch()
            return
        
        # Check that it is the same crash summary
        # TODO: this is a bit buggy with different levels of debug info between the two summaries
        # if not is_same_summary(crash_summary.strip(), summary.strip()):
        #     print(f'[{Colors.RED}FAIL{Colors.END}] {crash_path.name} :: (new) {crash_summary.strip()} != (orig) {summary.strip()}')
        #     (output_dir / '.minimized' / f'{crash_path.name}.fail').touch()
        #     return
        
        # 5. Write the external code
        (bucket_dir / 'repro' / f'{crash_path.name}.cpp').write_text(external_file.read_text())
        (output_dir / '.minimized' / f'{crash_path.name}.success').touch()


def run_minimize(info, fuzzer, crashes_dir, output_dir, nproc) -> bool:
    """For every processed crash, if the corresponding bucket has fewer than N reproducers, run the minimizer."""
    processed = set([f.stem for f in (output_dir / '.processed').iterdir() if f.is_file() and not f.name.startswith('.') and not f.name.endswith('.none')])
    minimized = set([f.stem for f in (output_dir / '.minimized').iterdir() if f.is_file() and not f.name.startswith('.')])
    new_processed = list(processed - minimized)

    if len(new_processed) == 0:
        return False

    print(f'[{Colors.GREEN}*{Colors.END}] Minimizing {len(new_processed)} crashes')

    for crash in tqdm(new_processed[:10], desc='Minimizing crashes'):
        minimize_crash((info, fuzzer, crashes_dir / crash, output_dir))
    
    return len(new_processed) > 10


def main(args):
    fuzzer = Path(args.fuzzer).absolute()
    crashes_dir = Path(args.crashes).absolute()
    output_dir = Path(args.output).absolute()
    nproc = args.nproc
    watch = args.watch

    # Create the directory structure if it doesn't exist
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / '.processed').mkdir(parents=True, exist_ok=True) # Indicates crashes that have been processed
    (output_dir / '.minimized').mkdir(parents=True, exist_ok=True) # Indicates crashes that have been minimized

    # Force the fuzzer to create the pspec cache so we don't hit a race condition in the multiprocess code
    subprocess.run([fuzzer, '--bypass-validation'], capture_output=False)

    while True:
        has_more = False
        has_more |= run_bucketize(fuzzer, crashes_dir, output_dir, nproc)
        has_more |= run_minimize(args.info, fuzzer, crashes_dir, output_dir, nproc)

        if not has_more:
            time.sleep(5)

        if not watch and not has_more:
            break


def register(subparsers: ArgumentParser):
    parser = subparsers.add_parser('bucketizer')
    parser.add_argument('--info', type=str, default='info.json', help='Path to the project info')
    parser.add_argument('--fuzzer', type=str, required=True)
    parser.add_argument('--crashes', type=str, required=True, help='Path to the crashes directory')
    parser.add_argument('--output', type=str, required=True, help='Path to the output directory (to store crash buckets)')
    parser.add_argument('--nproc', type=int, default=1, help='Number of processes to use')
    parser.add_argument('--watch', action='store_true', help='Continue watching for new crashes')
    parser.set_defaults(func=main)
