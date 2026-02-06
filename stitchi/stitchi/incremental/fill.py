from typing import List, Tuple, Dict, Set
import os

from pydantic import BaseModel

from ..data.metadata import UsageTracker, UsageMetrics
from ..data.project_info import ProjectInfo
from .data import FunctionStub, ObjectStub, FunctionImpl, SynthesisEvent, EventType
from .synth import validate_impl_build

from langchain_openai import ChatOpenAI
from langchain_core.callbacks import get_usage_metadata_callback


SPEC = open(os.path.join(os.path.dirname(__file__), 'spec.md')).read()


def analyze_missing_object_types(validated_endpoints: List[FunctionImpl], objects: List[ObjectStub], max_depth: int = 6) -> Set[str]:
    """Return set of object type names that are not constructible within depth."""
    # Build object type set and mapping
    object_names: List[str] = [o.type for o in (objects or [])]
    object_types: Dict[str, int] = {name: i for i, name in enumerate(object_names)}

    # Normalize endpoints: endpoint_name -> (inputs, outputs)
    endpoint_inputs: Dict[str, List[str]] = {}
    endpoint_outputs: Dict[str, List[str]] = {}
    types_seen: Set[str] = set()

    for ep in validated_endpoints or []:
        in_types: List[str] = [io.type for io in (ep.inputs or []) if io.type]
        out_types: List[str] = [io.type for io in (ep.outputs or []) if io.type]
        for t in in_types:
            types_seen.add(t)
        for t in out_types:
            types_seen.add(t)
        endpoint_inputs[ep.name] = in_types
        endpoint_outputs[ep.name] = out_types

    # Build producers map: type_name -> list of endpoints that produce it
    producers: Dict[str, List[str]] = {}
    for ep_name, outs in endpoint_outputs.items():
        for t in outs:
            producers.setdefault(t, []).append(ep_name)

    memo: Dict[Tuple[str, int], bool] = {}
    visiting: Set[Tuple[str, int]] = set()

    def type_constructible(tname: str, depth: int) -> bool:
        if depth == 0:
            return False
        key = (tname, depth)
        if key in memo:
            return memo[key]
        if key in visiting:
            memo[key] = False
            return False
        visiting.add(key)

        ok = False
        for ep_name in producers.get(tname, []):
            inputs_ok = True
            for it in endpoint_inputs.get(ep_name, []):
                if not type_constructible(it, depth - 1):
                    inputs_ok = False
                    break
            if inputs_ok:
                ok = True
                break

        visiting.remove(key)
        memo[key] = ok
        return ok

    all_types: Set[str] = set(object_types.keys()) | types_seen
    missing: Set[str] = set()
    for t in all_types:
        if not type_constructible(t, max_depth):
            missing.add(t)
    return missing


def analyze_blocking_missing_types(validated_endpoints: List[FunctionImpl], objects: List[ObjectStub], max_depth: int = 6) -> Set[str]:
    """Return only the missing object types that block some endpoint input.

    A type is considered blocking if it is (a) not constructible within depth and (b) appears
    as an input type for at least one endpoint.
    """
    # Normalize inputs/outputs and producers
    endpoint_inputs: Dict[str, List[str]] = {}
    endpoint_outputs: Dict[str, List[str]] = {}
    types_seen: Set[str] = set()

    for ep in validated_endpoints or []:
        in_types: List[str] = [io.type for io in (ep.inputs or []) if io.type]
        out_types: List[str] = [io.type for io in (ep.outputs or []) if io.type]
        for t in in_types:
            types_seen.add(t)
        for t in out_types:
            types_seen.add(t)
        endpoint_inputs[ep.name] = in_types
        endpoint_outputs[ep.name] = out_types

    producers: Dict[str, List[str]] = {}
    for ep_name, outs in endpoint_outputs.items():
        for t in outs:
            producers.setdefault(t, []).append(ep_name)

    memo: Dict[Tuple[str, int], bool] = {}
    visiting: Set[Tuple[str, int]] = set()

    def type_constructible(tname: str, depth: int) -> bool:
        if depth == 0:
            return False
        key = (tname, depth)
        if key in memo:
            return memo[key]
        if key in visiting:
            memo[key] = False
            return False
        visiting.add(key)

        ok = False
        for ep_name in producers.get(tname, []):
            inputs_ok = True
            for it in endpoint_inputs.get(ep_name, []):
                if not type_constructible(it, depth - 1):
                    inputs_ok = False
                    break
            if inputs_ok:
                ok = True
                break

        visiting.remove(key)
        memo[key] = ok
        return ok

    # Compute missing types across seen types and declared object types
    declared_types: Set[str] = {o.type for o in (objects or [])}
    all_types: Set[str] = declared_types | types_seen
    missing: Set[str] = set()
    for t in all_types:
        if not type_constructible(t, max_depth):
            missing.add(t)

    # Only return those missing types that are used as inputs to at least one endpoint
    input_types: Set[str] = set()
    for in_list in endpoint_inputs.values():
        input_types.update(in_list)

    blocking: Set[str] = missing & input_types
    return blocking


PROMPT_FILL_BATCH = """
You are an expert C/C++ fuzzer engineer.

You will be given the code block specification ([SPEC]), the full list of functions ([FUNCTIONS]) and the full list of objects ([OBJECTS]) for a target API, as well as the current list of endpoint code blocks ([ENDPOINTS]). Each endpoint is a C++ snippet that will be embedded in a fuzzing harness.

Task: For each object type listed in [TARGET_OBJECT_TYPES], determine how to construct if (if possible) and return a code block that constructs an instance of that object type according to the API.

If you cannot construct an object type, skip it.

Requirements:
- Determine how to construct the object type (i.e. either directly if it is user-managed or through a given constructor function if it is managed by the API)
- The endpoint must produce the target object type as an output (on the heap if needed), following the spec's Inputs/Outputs rules.
- Avoid using inputs unless absolutely necessary.
- Return one endpoint per target object type.

The endpoint `name` should be `construct_<target_type>`.

[SPEC]
{spec}
[/SPEC]

[FUNCTIONS]
{functions}
[/FUNCTIONS]

[OBJECTS]
{objects}
[/OBJECTS]

[AVAILABLE_INOUT_OBJECTS]
{inout_objects}
[/AVAILABLE_INOUT_OBJECTS]

[ENDPOINTS]
{endpoints}
[/ENDPOINTS]

[TARGET_OBJECT_TYPES]
{target_types}
[/TARGET_OBJECT_TYPES]
"""


class FillBatchResponse(BaseModel):
    endpoints: List[FunctionImpl]


def run_fill_batch(model: str, info: ProjectInfo, functions: List[FunctionStub], objects: List[ObjectStub], current_endpoints: List[FunctionImpl], target_types: List[str], usage: UsageTracker, events: List[SynthesisEvent]) -> List[FunctionImpl]:
    """Ask the LLM to synthesize constructor endpoints for target object types; return validated endpoints."""
    if not target_types:
        return []

    llm = ChatOpenAI(model=model).with_structured_output(FillBatchResponse, strict=True)

    prompt_vars = {
        'spec': SPEC,
        'functions': [f.model_dump_json() for f in functions],
        'objects': [o.model_dump_json() for o in objects],
        'inout_objects': [o.type for o in objects if len(o.direct_api_constructors) > 0],
        'endpoints': [e.model_dump_json() for e in current_endpoints],
        'target_types': target_types,
    }

    # Record attempt
    events.append(SynthesisEvent(
        event_type=EventType.SYNTHESIS_ATTEMPT,
        function_name='__fill_batch__',
        attempt_number=1,
        prompt_template=PROMPT_FILL_BATCH,
        prompt_variables=prompt_vars,
    ))

    with get_usage_metadata_callback() as cb:
        res: FillBatchResponse = llm.invoke(PROMPT_FILL_BATCH.format(**prompt_vars))
        usage.record('fill-batch', UsageMetrics.from_usage(cb.usage_metadata))

    proposed: List[FunctionImpl] = res.endpoints or []
    accepted: List[FunctionImpl] = []

    for impl in proposed:
        results = validate_impl_build(info, impl, objects, events)
        if results and results[0].valid:
            accepted.append(results[0].impl)
            events.append(SynthesisEvent(
                event_type=EventType.SYNTHESIS_SUCCESS,
                function_name=impl.name,
                attempt_number=1,
                cpp_code=impl.code,
            ))
        else:
            events.append(SynthesisEvent(
                event_type=EventType.SYNTHESIS_FAILURE,
                function_name=impl.name,
                attempt_number=1,
                error_message=(results[0].error if results else 'unknown error'),
            ))

    return accepted


