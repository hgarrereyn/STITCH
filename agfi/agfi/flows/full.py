from typing import List
from enum import Enum

from langchain_core.callbacks import get_usage_metadata_callback
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from ..data.project_info import ProjectInfo
from ..data.metadata import SchemaMetadata, UsageMetrics

from .find_objects import find_objects
from .find_functions import find_functions


def run_full(info: ProjectInfo, model: str, headers: List[str], threads: int) -> SchemaMetadata:
    meta = SchemaMetadata()

    # Find objects
    objects = find_objects(info, model, headers, meta.usage)
    meta.objects = objects

    # Find functions
    functions = find_functions(info, model, headers, threads, meta.usage)
    meta.functions = functions

    return meta


def main(args):
    info = ProjectInfo.model_validate_json(open(args.info).read())

    resp = run_full(info, args.model, args.headers, args.threads)
    
    # Write to file if provided
    if args.output:
        with open(args.output, 'w') as f:
            f.write(resp.model_dump_json(indent=2))
    else:
        print(resp.model_dump_json(indent=2))


def register(subparsers):
    parser = subparsers.add_parser('full', help='Run full mode')
    parser.add_argument('--info', type=str, default='info.json', help='Path to the project info file')
    parser.add_argument('--headers', type=str, nargs='+', required=True, help='List of header files to scan')
    parser.add_argument('--model', type=str, default='gpt-4.1-mini', help='Model to use for scanning')
    parser.add_argument('--output', type=str, default=None, help='Path to the output file')
    parser.add_argument('--threads', type=int, default=10, help='Number of threads to use for scanning')
    parser.set_defaults(func=main)
