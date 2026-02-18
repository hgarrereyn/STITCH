
from typing import List

from ..data.project_info import ProjectInfo
from .data import ProjectMetadata

def main(args):
    meta = ProjectMetadata.model_validate_json(open(args.meta).read())

    schema = meta.to_schema(run_generators=not args.no_generators)

    if args.output:
        with open(args.output, 'w') as f:
            f.write(schema.model_dump_json(indent=2))
    else:
        print(schema.model_dump_json(indent=2))


def register(subparsers):
    parser = subparsers.add_parser('to-schema', help='Convert a project info to a schema metadata')
    parser.add_argument('--meta', type=str, required=True, help='Path to the schema metadata file')
    parser.add_argument('--output', type=str, default=None, help='Path to the output file')
    parser.add_argument('--no-generators', action='store_true', help='Skip running the generators')
    parser.set_defaults(func=main)
