
from typing import List

from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from ..data.project_info import ProjectInfo
from ..data.metadata import FunctionStubTemplate, SchemaMetadata, FunctionStubMapping, FunctionStub, FunctionArgumentUsage
from ..utils.colors import Colors
from ..utils.general import split_into_chunks
from ..build.validator import validate_function, format_and_check
from ..data.schema import Spec, Object, Attr, Endpoint, OutlivesConstraint, EndpointRole, VariableDir, Variable


def main(args):
    info = ProjectInfo.model_validate_json(open(args.info).read())
    meta = SchemaMetadata.model_validate_json(open(args.meta).read())

    schema = meta.to_schema()

    if args.output:
        with open(args.output, 'w') as f:
            f.write(schema.model_dump_json(indent=2))
    else:
        print(schema.model_dump_json(indent=2))


def register(subparsers):
    parser = subparsers.add_parser('to-schema', help='Convert a project info to a schema metadata')
    parser.add_argument('--info', type=str, default='info.json', help='Path to the project info file')
    parser.add_argument('--meta', type=str, required=True, help='Path to the schema metadata file')
    parser.add_argument('--output', type=str, default=None, help='Path to the output file')
    parser.set_defaults(func=main)
