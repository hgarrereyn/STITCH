from pydantic import BaseModel
import json
import os
from typing import List

from ..data.metadata import UsageTracker, UsageTokens
from .data import DataGenerator, ObjectInfo, ProjectMetadata, FunctionImpl, FunctionInfo, DataTypeInfo, FunctionClassifier
from .pretty import pretty_print_generator, pretty_print_code
from ..utils.colors import Colors
from .util import sample_generator, validate_generator

from openai import OpenAI


REGEN_ATTEMPTS = 3

PROMPT_BUILD_GENERATOR = open(os.path.join(os.path.dirname(__file__), 'prompts/build_generator.md')).read()


class BuildGeneratorResponse(BaseModel):
    generator: str


def build_generators(objects: List[ObjectInfo], functions: List[FunctionInfo], endpoints: List[FunctionImpl], endpoint_classifiers: List[FunctionClassifier], data_types: List[DataTypeInfo], usage: UsageTracker) -> List[DataGenerator]:
    client = OpenAI()

    # Group endpoints by data type
    endpoints_by_name = {e.name: e for e in endpoints}
    endpoint_classifiers_by_name = {c.name: c for c in endpoint_classifiers}
    data_types_by_name = {d.data_type: d for d in data_types}

    by_type = {}
    for name in endpoint_classifiers_by_name:
        for data_type in endpoint_classifiers_by_name[name].data_types:
            if data_type not in by_type:
                by_type[data_type] = []
            by_type[data_type].append(endpoints_by_name[name])

    generators: List[DataGenerator] = []

    # Synthesize generators for each data type
    for data_type in by_type:
        p_objects = [o.model_dump_json() for o in objects]
        p_functions = [f.model_dump_json() for f in functions]
        p_endpoints = [e.model_dump_json() for e in by_type[data_type]]
        p_data_type = data_types_by_name[data_type].model_dump_json()

        context = [
            {
                "role": "developer",
                "content": PROMPT_BUILD_GENERATOR
            },
            {
                "role": "user",
                "content": f'Objects: {json.dumps(p_objects)}\nFunctions: {json.dumps(p_functions)}\Example fuzzer stubs: {json.dumps(p_endpoints)}\nData type: {json.dumps(p_data_type)}'
            },
        ]
        
        generator = None
        for i in range(REGEN_ATTEMPTS):
            print(f'[{Colors.GREEN}+{Colors.END}] Building generator for {data_type} (attempt {i+1})')
            response = client.responses.parse(
                model="gpt-5",
                reasoning={ "effort": "minimal" },
                input=context,
                text_format=BuildGeneratorResponse
            )
            usage.record_model('build-generators', 'gpt-5', UsageTokens.from_openai(response.usage))
            
            generator = response.output_parsed.generator
            error = validate_generator(generator)
            if error is not None:
                print(f'[{Colors.RED}-{Colors.END}] Error validating generator for {data_type}: {error}')

                pretty_print_code(generator, language="python")
                
                # Append assistant output and the error result
                context.append({
                    "role": "assistant",
                    "content": generator
                })
                context.append({
                    "role": "user",
                    "content": f'Error validating generator for {data_type}: {error}'
                })
            else:
                print(f'[{Colors.GREEN}+{Colors.END}] Generator for {data_type} built successfully')
                break

        gen = DataGenerator(data_type=data_type, generator=generator)

        pretty_print_generator(gen)
        samples = sample_generator(generator, n=10)
        for i, sample in enumerate(samples):
            print(f'[{Colors.CYAN}*{Colors.END}] Sample {i}: {sample}')

        generators.append(gen)
        
    return generators


def main_build_generators(args):
    harness = ProjectMetadata.model_validate_json(open(args.harness).read())

    generators = build_generators(harness.objects, harness.functions, harness.endpoints, harness.endpoint_classifiers, harness.data_types, harness.usage)
    print(harness.usage.cost_by_task())

    harness.generators = generators
    with open(args.output, 'w') as f:
        f.write(harness.model_dump_json(indent=2))


def register(subparsers):
    test_hint_parser = subparsers.add_parser('build-generators')
    test_hint_parser.add_argument('--harness', type=str, required=True)
    test_hint_parser.add_argument('--output', type=str, required=True)
    test_hint_parser.set_defaults(func=main_build_generators)
