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
from .utils.colors import Colors


def find_installed_libs():
    # Recursively find all .so files in /fuzz/install/lib
    return [str(f) for f in Path('/fuzz/install/lib').rglob('*.so')]


def process_queue_item(args):
    fuzzer, queue_item, coverage_dir, verbose = args

    name = Path(queue_item).stem

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_dir = Path(tmp_dir)
        tmp_dir.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env['LLVM_PROFILE_FILE'] = str(tmp_dir / 'coverage.profdata')

        subprocess.run([
            'timeout', '15', fuzzer, '-e', queue_item, '--bypass-validation'
        ], env=env, capture_output=not verbose)

        subprocess.run([
            'llvm-profdata-17', 'merge', '-sparse', str(tmp_dir / 'coverage.profdata'), '-o', str(coverage_dir / f'{name}.profdata')
        ], capture_output=not verbose)


def run_cover(args):
    fuzzer = Path(args.fuzzer).absolute()
    queue_dir = Path(args.queue).absolute()
    coverage_dir = Path(args.coverage).absolute()

    profdata_dir = coverage_dir / 'profdata'
    profdata_dir.mkdir(parents=True, exist_ok=True)

    nproc = args.nproc

    # Force the fuzzer to create the pspec cache so we don't hit a race condition in the multiprocess code
    subprocess.run([fuzzer, '--bypass-validation'], capture_output=False)

    # Read all non-hidden files in the queue directory
    queue = set([f.name for f in queue_dir.iterdir() if f.is_file() and not f.name.startswith('.')])        
    processed = set([f.stem for f in profdata_dir.iterdir() if f.is_file() and not f.name.startswith('.')])

    new_queue = queue - processed

    if len(new_queue) > 0:
        print(f'[{Colors.GREEN}*{Colors.END}] Processing {len(new_queue)} queue items')
        
        with Pool(nproc) as p:
            for _ in tqdm(p.imap_unordered(process_queue_item, [(fuzzer, queue_dir / c, profdata_dir, args.verbose) for c in new_queue]), total=len(new_queue), desc='Processing queue'):
                pass

    # Fetch all profdata files in the coverage directory
    profdata_files = [f for f in profdata_dir.iterdir() if f.is_file() and f.name.endswith('.profdata')]
    if len(profdata_files) == 0:
        print(f'[{Colors.RED}-{Colors.END}] No profdata files found in the coverage directory')
        return
    
    print(f'[{Colors.GREEN}*{Colors.END}] Merging {len(profdata_files)} profdata files')
    # Merge all profdata files into a single profdata file
    subprocess.run([
        'llvm-profdata-17', 'merge', '-sparse', *profdata_files, '-o', str(coverage_dir / 'merged.profdata')
    ], capture_output=True)


    cov_html = coverage_dir / 'html'
    cov_html.mkdir(parents=True, exist_ok=True)

    objs = find_installed_libs()

    print(f'[{Colors.GREEN}*{Colors.END}] Generating HTML report')
    # Generate an html report
    try:
        cmd = [
            'llvm-cov-17', 'show', str(fuzzer),
            '-format=html', '-output-dir', str(cov_html),
            '-instr-profile', str(coverage_dir / 'merged.profdata'),
            '-show-line-counts-or-regions',
            '-ignore-filename-regex', r'^/fuzz/workspace',
            # '-path-equivalence', '/fuzz/src,.'
        ]
        for obj in objs:
            cmd.append('-object')
            cmd.append(obj)
        
        subprocess.run(cmd, capture_output=False)
    except Exception as e:
        print(f'[{Colors.RED}-{Colors.END}] Error generating HTML report: {e}')

    print(f'[{Colors.GREEN}*{Colors.END}] Generating JSON report')
    # Generate a JSON report
    try:
        cmd = [
            'llvm-cov-17', 'export', str(fuzzer),
            '-instr-profile', str(coverage_dir / 'merged.profdata'),
            '-format=text',
            '-ignore-filename-regex', r'^/fuzz/workspace',
            # '-path-equivalence', '/fuzz/src,.'
        ]
        for obj in objs:
            cmd.append('-object')
            cmd.append(obj)
        res = subprocess.run(cmd, capture_output=True)
        with open(coverage_dir / 'coverage.json', 'w') as f:
            f.write(res.stdout.decode('utf-8'))
    except Exception as e:
        print(f'[{Colors.RED}-{Colors.END}] Error generating JSON report: {e}')

    print(f'[{Colors.GREEN}*{Colors.END}] Done')


def register(subparsers):
    parser = subparsers.add_parser('run-cover', help='Compute coverage for a project')
    parser.add_argument('fuzzer', type=str, help='Fuzzer binary')
    parser.add_argument('queue', type=str, help='Queue directory')
    parser.add_argument('coverage', type=str, help='Coverage directory')
    parser.add_argument('--nproc', type=int, default=1, help='Number of processes to use')
    parser.add_argument('--verbose', action='store_true', help='Verbose output')
    parser.set_defaults(func=run_cover)
