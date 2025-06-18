
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

    mappings = {x.name: x for x in meta.mapping_functions}
    templates = {x.name: x for x in meta.template_functions}
    hints = {x.name: x for x in meta.hints_functions}
    schema = Spec(
        objects=[],
        endpoints=[],
    )

    for obj in meta.objects:
        if obj.lifetime == 'ephemeral':
            continue

        schema.objects.append(Object(
            name=obj.type,
            attrs=[],
        ))

    for func in templates.values():
        mapping = mappings.get(func.name)
        hint = hints.get(func.name)
        vars = []
        for arg in [*mapping.arguments, mapping.return_value]:
            if arg.canonical_type == 'none':
                continue

            obj = next((x for x in meta.objects if x.type == arg.canonical_type), None)
            if obj is None:
                raise ValueError(f'Object {arg.canonical_type} not found in objects')

            if obj.lifetime == 'ephemeral':
                continue

            if arg.usage == FunctionArgumentUsage.DEFAULT:
                vars.append(Variable(name=arg.name, type=arg.canonical_type, dir=VariableDir.InOut))
            elif arg.usage == FunctionArgumentUsage.NEW:
                vars.append(Variable(name=arg.name, type=arg.canonical_type, dir=VariableDir.New))
            elif arg.usage == FunctionArgumentUsage.DELETE:
                vars.append(Variable(name=arg.name, type=arg.canonical_type, dir=VariableDir.Del))

        schema.endpoints.append(Endpoint(
            name=func.name,
            variables=vars,
            template=func.stub,
            hints=hint.hints if hint else [],
            preconditions=[],
            effects=[],
            role=EndpointRole.NONE,
            outlives=[],
        ))

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
