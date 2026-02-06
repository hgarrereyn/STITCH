from pathlib import Path
import difflib
from ..data.project_info import ProjectInfo
from .looper import CrashInfo
from pydantic import BaseModel
from .distill_agent import CrashAssessment
from typing import Optional
from .patcher import PatchInfo
from ..utils.colors import Colors
from .pretty import pretty_print_code
from .data import ProjectMetadata
from .util import sample_generator



def main(args):
    harness = ProjectMetadata.model_validate_json(open(args.harness).read())

    # Print generators
    if harness.generators is not None:
        print(f'{Colors.GREEN}[+]{Colors.END} Generators:')
        for generator in harness.generators:
            print(f'    {Colors.CYAN}[+]{Colors.END} Generator for {Colors.YELLOW}{generator.data_type}{Colors.END}:')
            pretty_print_code(generator.generator, language="python")

            examples = sample_generator(generator.generator, n=2)
            for example in examples:
                print(f'    {Colors.CYAN}[+]{Colors.END} Example: {Colors.YELLOW}{example}{Colors.END}')


def register(subparsers):
    parser = subparsers.add_parser('harness-info')
    parser.add_argument('--harness', type=str, required=True, help='Path to the harness')
    parser.set_defaults(func=main)
