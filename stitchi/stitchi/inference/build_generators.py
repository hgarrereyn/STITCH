from __future__ import annotations

from pydantic import BaseModel
import json
import os
from typing import List, Optional, TYPE_CHECKING

from ..data.metadata import UsageTracker
from .data import DataGenerator, ObjectInfo, ProjectMetadata, FunctionImpl, FunctionInfo, DataTypeInfo, FunctionClassifier
from .pretty import pretty_print_generator, pretty_print_code
from .util import sample_generator, validate_generator, get_model, call_llm

if TYPE_CHECKING:
    from .ui import TokenProgress


REGEN_ATTEMPTS = 3

PROMPT_BUILD_GENERATOR = open(os.path.join(os.path.dirname(__file__), 'prompts/build_generator.md')).read()


class BuildGeneratorResponse(BaseModel):
    generator: str


def build_generator(
    data_type_info: DataTypeInfo,
    objects: List[ObjectInfo],
    functions: List[FunctionInfo],
    endpoints: List[FunctionImpl],
    endpoint_classifiers: List[FunctionClassifier],
    usage: UsageTracker,
    selective_context: bool = False,
    quiet: bool = False,
    token_progress: Optional[TokenProgress] = None,
) -> List[DataGenerator]:
    usage = UsageTracker()

    data_type = data_type_info.data_type
    
    endpoints_by_name = {e.name: e for e in endpoints}
    endpoint_classifiers_by_name = {c.name: c for c in endpoint_classifiers}

    by_type = {}
    for name in endpoint_classifiers_by_name:
        for dt in endpoint_classifiers_by_name[name].data_types:
            if dt not in by_type:
                by_type[dt] = []
            by_type[dt].append(endpoints_by_name[name])

    if data_type not in by_type:
        return []

    ctx = [
        {
            "role": "developer",
            "content": PROMPT_BUILD_GENERATOR
        }
    ]
    if selective_context:
        relevant_functions = [f for f in functions if f.name in by_type[data_type]]
        p_objects = [o.model_dump_json() for o in objects]
        p_functions = [f.model_dump_json() for f in relevant_functions]
        p_endpoints = [e.model_dump_json() for e in by_type[data_type]]
        p_data_type = data_type_info.model_dump_json()

        ctx.append({
            "role": "user",
            "content": f'Objects: {json.dumps(p_objects)}\nFunctions: {json.dumps(p_functions)}\nTarget fuzzer stubs: {json.dumps(p_endpoints)}\nKnown data types: {json.dumps(p_data_type)}'
        })
    else:
        p_objects = [o.model_dump_json() for o in objects]
        p_functions = [f.model_dump_json() for f in functions]
        p_endpoints = [e.model_dump_json() for e in by_type[data_type]]
        p_data_type = data_type_info.model_dump_json()

        ctx.append({
            "role": "user",
            "content": f'Objects: {json.dumps(p_objects)}\nFunctions: {json.dumps(p_functions)}\nTarget fuzzer stubs: {json.dumps(p_endpoints)}\nKnown data types: {json.dumps(p_data_type)}'
        })
    
    validated = False
    generator = None
    model = get_model()
    for i in range(REGEN_ATTEMPTS):
        parsed: BuildGeneratorResponse = call_llm(
            model=model,
            input=ctx,
            text_format=BuildGeneratorResponse,
            usage=usage,
            task='build-generators',
            reasoning={"effort": "low"},
            token_progress=token_progress,
        )

        generator = parsed.generator
        error = validate_generator(generator)
        if error is not None:
            if not quiet:
                pretty_print_code(generator, language="python")

            ctx.append({
                "role": "assistant",
                "content": generator
            })
            ctx.append({
                "role": "user",
                "content": f'Error validating generator for {data_type}: {error}'
            })
        else:
            validated = True
            break

    if not validated:
        return []

    gen = DataGenerator(data_type=data_type, generator=generator)

    if not quiet:
        pretty_print_generator(gen)
        samples = sample_generator(generator, n=10)
        for i, sample in enumerate(samples):
            from . import ui
            ui.info(f"Sample {i}: {sample}")

    return [gen]


def main_build_generators(args):
    from . import ui

    harness = ProjectMetadata.model_validate_json(open(args.harness).read())

    generators = ui.parallel_run(
        args.threads, build_generator,
        [(d, harness.objects, harness.functions, harness.endpoints,
          harness.endpoint_classifiers, harness.usage, args.selective_context, False)
         for d in harness.data_types],
        item_labels=[d.data_type for d in harness.data_types],
        ok=lambda r: len(r) > 0,
    )
    print(harness.usage.cost_by_task())

    harness.generators = generators
    with open(args.output, 'w') as f:
        f.write(harness.model_dump_json(indent=2))


def register(subparsers):
    test_hint_parser = subparsers.add_parser('build-generators')
    test_hint_parser.add_argument('--harness', type=str, required=True)
    test_hint_parser.add_argument('--output', type=str, required=True)
    test_hint_parser.add_argument('--threads', type=int, default=1)
    test_hint_parser.add_argument('--selective-context', action='store_true', default=False)
    test_hint_parser.set_defaults(func=main_build_generators)
