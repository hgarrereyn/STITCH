from pydantic import BaseModel
import json
import os
from typing import List, Optional, Tuple

from ..data.metadata import UsageTracker, UsageTokens
from .data import ObjectInfo, ProjectMetadata, FunctionImpl, FunctionInfo, FunctionClassifier, DataTypeInfo
from ..utils.colors import Colors

from openai import OpenAI


BATCH_SIZE = 20

PROMPT_CLASSIFY_DATA = open(os.path.join(os.path.dirname(__file__), 'prompts/classify_data.md')).read()


class ClassificationResponse(BaseModel):
    classifiers: List[FunctionClassifier]
    new_data_types: List[DataTypeInfo]


def classify_data(objects: List[ObjectInfo], functions: List[FunctionInfo], endpoints: List[FunctionImpl], usage: UsageTracker, selective_context: bool = False) -> Tuple[List[DataTypeInfo], List[FunctionClassifier]]:
    client = OpenAI()

    data_types: List[DataTypeInfo] = []
    classifiers: List[FunctionClassifier] = []

    # Only run endpoints that have FUZZ_PARAM_STR or FUZZ_PARAM_FILENAME calls
    remaining_endpoints: List[FunctionImpl] = []
    for e in endpoints:
        if 'FUZZ_PARAM_STR' in e.code or 'FUZZ_PARAM_FILENAME' in e.code:
            remaining_endpoints.append(e)
        else:
            # Empty classifier
            classifiers.append(FunctionClassifier(name=e.name, data_types=[]))

    remaining_names = set([e.name for e in remaining_endpoints])

    for i in range(0, len(remaining_endpoints), BATCH_SIZE):
        print(f'Classifying data (step {i + 1} of {len(remaining_endpoints)})...')
        batch_endpoints = remaining_endpoints[i:i+BATCH_SIZE]

        ctx = [
            {
                "role": "developer",
                "content": PROMPT_CLASSIFY_DATA
            }
        ]
        if selective_context:
            batch_endpoint_names = [e.name for e in batch_endpoints]
            relevant_functions = [f for f in functions if f.name in batch_endpoint_names]

            p_objects = [o.model_dump_json() for o in objects]
            p_functions = [f.model_dump_json() for f in relevant_functions]
            p_endpoints = [e.model_dump_json() for e in batch_endpoints]
            p_data_types = [d.model_dump_json() for d in data_types]

            ctx.append({
                "role": "user",
                "content": f'Objects: {json.dumps(p_objects)}\nFunctions: {json.dumps(p_functions)}\nTarget fuzzer stubs: {json.dumps(p_endpoints)}\nKnown data types: {json.dumps(p_data_types)}'
            })
        else:
            p_objects = [o.model_dump_json() for o in objects]
            p_functions = [f.model_dump_json() for f in functions]
            p_endpoints = [e.model_dump_json() for e in batch_endpoints]
            p_data_types = [d.model_dump_json() for d in data_types]
            
            ctx.append({
                "role": "user",
                "content": f'Objects: {json.dumps(p_objects)}\nFunctions: {json.dumps(p_functions)}\nTarget fuzzer stubs: {json.dumps(p_endpoints)}\nKnown data types: {json.dumps(p_data_types)}'
            })

        response = client.responses.parse(
            model="gpt-5",
            reasoning={ "effort": "minimal" },
            input=ctx,
            text_format=ClassificationResponse
        )
        usage.record_model('classify-data', 'gpt-5', UsageTokens.from_openai(response.usage))

        # Validate the classifiers match expected function names
        for c in response.output_parsed.classifiers:
            if c.name not in remaining_names:
                print(f'{Colors.RED}Function {Colors.YELLOW}{c.name}{Colors.END} not found in remaining names, skipping...')
                continue

            remaining_names.remove(c.name)
            classifiers.append(c)

            print(f'{Colors.GREEN}Classified function {Colors.YELLOW}{c.name}{Colors.END} with data types: {Colors.YELLOW}{c.data_types}{Colors.END}')

        # Validate the new data types aren't duplicates
        for d in response.output_parsed.new_data_types:
            if d.data_type in data_types:
                print(f'{Colors.RED}Data type {Colors.YELLOW}{d.data_type}{Colors.END} already exists, skipping...')
                continue
            data_types.append(d)
            print(f'{Colors.GREEN}Added new data type {Colors.YELLOW}{d.data_type}{Colors.END} with description: {Colors.YELLOW}{d.description}{Colors.END}')

    for n in remaining_names:
        print(f'{Colors.RED}Function {Colors.YELLOW}{n}{Colors.END} missed')
        classifiers.append(FunctionClassifier(name=n, data_types=[]))

    return data_types, classifiers


def main_classify_data(args):
    harness = ProjectMetadata.model_validate_json(open(args.harness).read())

    data_types, classifiers = classify_data(harness.objects, harness.functions, harness.endpoints, harness.usage)
    print(harness.usage.cost_by_task())

    harness.endpoint_classifiers = classifiers
    harness.data_types = data_types
    
    with open(args.output, 'w') as f:
        f.write(harness.model_dump_json(indent=2))


def register(subparsers):
    classify_parser = subparsers.add_parser('classify-data')
    classify_parser.add_argument('--harness', type=str, required=True)
    classify_parser.add_argument('--output', type=str, required=True)
    classify_parser.set_defaults(func=main_classify_data)
