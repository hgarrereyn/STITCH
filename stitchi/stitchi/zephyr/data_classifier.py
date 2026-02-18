from __future__ import annotations

from pydantic import BaseModel
import json
import os
from typing import List, Optional, Tuple, TYPE_CHECKING

from ..data.metadata import UsageTracker
from .data import ObjectInfo, ProjectMetadata, FunctionImpl, FunctionInfo, FunctionClassifier, DataTypeInfo
from .util import call_llm

if TYPE_CHECKING:
    from .ui import TokenProgress


BATCH_SIZE = 20

PROMPT_CLASSIFY_DATA = open(os.path.join(os.path.dirname(__file__), 'prompts/classify_data.md')).read()


class ClassificationResponse(BaseModel):
    classifiers: List[FunctionClassifier]
    new_data_types: List[DataTypeInfo]


def classify_data(objects: List[ObjectInfo], functions: List[FunctionInfo], endpoints: List[FunctionImpl], usage: UsageTracker, selective_context: bool = False, token_progress: Optional[TokenProgress] = None) -> Tuple[List[DataTypeInfo], List[FunctionClassifier]]:
    from . import ui

    data_types: List[DataTypeInfo] = []
    classifiers: List[FunctionClassifier] = []

    remaining_endpoints: List[FunctionImpl] = []
    for e in endpoints:
        if 'FUZZ_PARAM_STR' in e.code or 'FUZZ_PARAM_FILENAME' in e.code:
            remaining_endpoints.append(e)
        else:
            classifiers.append(FunctionClassifier(name=e.name, data_types=[]))

    remaining_names = set([e.name for e in remaining_endpoints])

    if not remaining_endpoints:
        ui.info("No endpoints require data classification")
        return data_types, classifiers

    total_batches = (len(remaining_endpoints) + BATCH_SIZE - 1) // BATCH_SIZE
    for batch_idx, i in enumerate(range(0, len(remaining_endpoints), BATCH_SIZE)):
        batch_endpoints = remaining_endpoints[i:i+BATCH_SIZE]
        batch_size = len(batch_endpoints)

        if total_batches > 1:
            ui.step_header(
                f"Classification batch {batch_idx + 1} of {total_batches}  ·  "
                f"[cyan]{batch_size}[/cyan] endpoints"
            )

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

        tp = token_progress or ui.TokenProgress()
        with ui.spinner(f"Classifying endpoints (batch of {batch_size})", token_progress=tp):
            parsed: ClassificationResponse = call_llm(
                input=ctx,
                text_format=ClassificationResponse,
                usage=usage,
                task='classify-data',
                reasoning={"effort": "low"},
                token_progress=tp,
            )

        n_classified = 0
        for c in parsed.classifiers:
            if c.name not in remaining_names:
                continue
            remaining_names.remove(c.name)
            classifiers.append(c)
            n_classified += 1

        for d in parsed.new_data_types:
            if d.data_type in data_types:
                continue
            data_types.append(d)

        ui.success(
            f"[green]{n_classified}[/green] classified  ·  "
            f"[cyan]{len(parsed.new_data_types)}[/cyan] new data types"
        )

    for n in remaining_names:
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
