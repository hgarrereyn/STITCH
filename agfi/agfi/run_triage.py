import os
import shutil
import subprocess
import tempfile
import hashlib
import json
import time
from collections import defaultdict
from typing import Optional
from pathlib import Path
from pydantic import BaseModel
from tqdm import tqdm
from multiprocessing import Pool
from .utils.crash_repro import try_get_crash_result, try_generate_reproducer, CrashResult
from .utils.colors import Colors


REPRO_PER_CRASH = 15
REPRO_FAIL_PER_CRASH = 15

MIN_PER_CRASH = 1
MIN_FAIL_PER_CRASH = 3


def process_crash(args):
    fuzzer, crash_path, reports_dir = args

    res = try_get_crash_result(fuzzer, crash_path)
    if res is None:
        (reports_dir / f'{crash_path.name}.report').touch()
    else:
        (reports_dir / f'{crash_path.name}.report').write_text(res.crash_summary + '\n')


def generate_reproducer(args):
    fuzzer, crash_path, summary, bucket_dir = args
    bucket_dir = Path(bucket_dir)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_dir = Path(tmp_dir)

        res = try_generate_reproducer(tmp_dir, fuzzer, crash_path)
        if res is None:
            (bucket_dir / 'repro' / f'{crash_path.name}.fail').touch() # Failed to generate a reproducer
        else:
            (bucket_dir / 'repro' / f'{crash_path.name}.cpp').write_text(res.external_code)
            (bucket_dir / 'repro' / f'{crash_path.name}.report').write_text(res.crash_report)


def run_triage(args):
    fuzzer = Path(args.fuzzer).absolute()
    crashes_dir = Path(args.crashes).absolute()
    output_dir = Path(args.output).absolute()
    nproc = args.nproc
    watch = args.watch

    reports_dir = output_dir / 'reports' # Stores crash reports for the interpreted test cases
    buckets_dir = output_dir / 'buckets' # Stores hashed-buckets for each crash

    reports_dir.mkdir(parents=True, exist_ok=True)
    buckets_dir.mkdir(parents=True, exist_ok=True)

    # Force the fuzzer to create the pspec cache so we don't hit a race condition in the multiprocess code
    subprocess.run([fuzzer, '--bypass-validation'], capture_output=False)

    while True:
        # Read all non-hidden files in the crashes directory
        crashes = set([f.name for f in crashes_dir.iterdir() if f.is_file() and not f.name.startswith('.')])        
        processed = set([f.stem for f in reports_dir.iterdir() if f.is_file() and not f.name.startswith('.')])

        new_crashes = crashes - processed

        if len(new_crashes) > 0:
            print(f'[{Colors.GREEN}*{Colors.END}] Processing {len(new_crashes)} crashes')
            
            with Pool(nproc) as p:
                for _ in tqdm(p.imap_unordered(process_crash, [(fuzzer, crashes_dir / c, reports_dir) for c in new_crashes]), total=len(new_crashes), desc='Processing crashes'):
                    pass
        elif watch:
            # Delay a bit
            time.sleep(5)
            continue

        bucket_counts = defaultdict(lambda: (0, 0)) # {hash: (repro, fail)}
        bucket_attempts = defaultdict(set) # {hash: set(reports)}
        for bucket_dir in buckets_dir.iterdir():
            fail_count = len(list((bucket_dir / 'repro').glob('*.fail')))
            success_count = len(list((bucket_dir / 'repro').glob('*.cpp')))
            bucket_counts[bucket_dir.name] = (success_count, fail_count)
            bucket_attempts[bucket_dir.name] = set([f.stem for f in (bucket_dir / 'repro').iterdir()])

        need_repro = []

        # Scan through reports and fill buckets as necessary
        for report_path in reports_dir.iterdir():
            report = report_path.read_text().strip()
            if report == '':
                continue

            bucket_hash = hashlib.sha256(report.encode()).hexdigest()[:8]

            bucket_dir = buckets_dir / bucket_hash
            if not bucket_dir.exists():
                print(f'[{Colors.GREEN}NEW{Colors.END}][{Colors.YELLOW}{bucket_hash}{Colors.END}] ({Colors.CYAN}{report_path.stem}{Colors.END}) :: {report}')
                bucket_dir.mkdir(parents=True, exist_ok=True)
                (bucket_dir / 'desc.txt').write_text(report + '\n')
                (bucket_dir / 'repro').mkdir(parents=True, exist_ok=True)
                (bucket_dir / 'minimized').mkdir(parents=True, exist_ok=True)

            if report_path.stem in bucket_attempts[bucket_hash]:
                # Skip if we've already attempted this report
                continue

            # Check if we have fewer than N reproducers for this bucket
            success_count, fail_count = bucket_counts[bucket_hash]
            if success_count < REPRO_PER_CRASH and fail_count < REPRO_FAIL_PER_CRASH:
                need_repro.append((report_path.stem, report, str(bucket_dir)))
                # Assume this will succeed (may be fixed in future iteration)
                bucket_counts[bucket_hash] = (success_count + 1, fail_count)
                bucket_attempts[bucket_hash].add(report_path.stem)

        with Pool(nproc) as p:
            for _ in tqdm(p.imap_unordered(generate_reproducer, [(fuzzer, crashes_dir / c, summary, bucket_dir) for (c,summary,bucket_dir) in need_repro]), total=len(need_repro), desc='Generating reproducers'):
                pass

        # Do llm minimization as necessary
        bucket_counts = defaultdict(lambda: (0, 0)) # {hash: (repro, fail)}
        bucket_attempts = defaultdict(set) # {hash: set(reports)}
        for bucket_dir in buckets_dir.iterdir():
            fail_count = len(list((bucket_dir / 'minimized').glob('*.fail')))
            success_count = len(list((bucket_dir / 'minimized').glob('*.cpp')))
            bucket_counts[bucket_dir.name] = (success_count, fail_count)
            bucket_attempts[bucket_dir.name] = set([f.stem for f in (bucket_dir / 'minimized').iterdir()])

        need_minimize = []

        # Scan through buckets and fill as necessary
        for bucket_dir in buckets_dir.iterdir():
            available_external = list((bucket_dir / 'repro').glob('*.cpp'))
    
            for external_file in available_external:
                if external_file.stem in bucket_attempts[bucket_dir.name]:
                    # Skip if we've already attempted this external file
                    continue

                success_count, fail_count = bucket_counts[bucket_dir.name]
                if success_count < MIN_PER_CRASH and fail_count < MIN_FAIL_PER_CRASH:
                    need_minimize.append((external_file.absolute(), bucket_dir))
                    bucket_counts[bucket_dir.name] = (success_count + 1, fail_count)
                    bucket_attempts[bucket_dir.name].add(external_file.stem)

        for external_file, bucket_dir in need_minimize:
            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_dir = Path(tmp_dir)
                out_file = tmp_dir / 'minimized.cpp'
                out_report = tmp_dir / 'minimized.report'
                
                subprocess.run([
                    'agfi', 'flow', 'llm-minimize',
                    external_file,
                    '--out_file', str(out_file), 
                    '--out_report', str(out_report)], 
                    check=True, capture_output=False
                )
                
                if not out_file.exists():
                    (bucket_dir / 'minimized' / f'{external_file.stem}.fail').touch()
                else:
                    print(f'[{Colors.GREEN}MIN{Colors.END}][{Colors.YELLOW}{bucket_dir.name}{Colors.END}] ({Colors.CYAN}{external_file.stem}{Colors.END})')
                    print('-' * 60)
                    print(out_file.read_text())
                    print('-' * 60)
                    (bucket_dir / 'minimized' / f'{external_file.stem}.cpp').write_text(out_file.read_text())
                    (bucket_dir / 'minimized' / f'{external_file.stem}.report').write_text(out_report.read_text())

        if not watch:
            break


def register(subparsers):
    parser = subparsers.add_parser('run-triage', help='Triage a project')
    parser.add_argument('fuzzer', type=str, help='Fuzzer binary')
    parser.add_argument('crashes', type=str, help='Crashes directory')
    parser.add_argument('output', type=str, help='Output directory')
    parser.add_argument('--nproc', type=int, default=1, help='Number of processes to use')
    parser.add_argument('--watch', action='store_true', help='Continue watching for new crashes')
    parser.set_defaults(func=run_triage)
