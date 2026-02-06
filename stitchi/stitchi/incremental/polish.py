from typing import List, Tuple, Dict, Optional
import os

from pydantic import BaseModel

from ..data.metadata import UsageTracker, UsageMetrics
from ..data.project_info import ProjectInfo
from .data import FunctionStub, ObjectStub, FunctionImpl, PolishChange, SynthesisEvent, EventType
from .synth import validate_impl_build

from langchain_openai import ChatOpenAI
from langchain_core.callbacks import get_usage_metadata_callback


SPEC = open(os.path.join(os.path.dirname(__file__), 'spec.md')).read()


PROMPT_POLISH_BATCH = """
You are an expert C/C++ fuzzer engineer.

You will be given the code block specification ([SPEC]), the full list of functions ([FUNCTIONS]) and the full list of objects ([OBJECTS]) for a target API, as well as the current list of endpoint code blocks ([ENDPOINTS]). Each endpoint is a C++ snippet that will be embedded in a fuzzing harness.

Task: Review ONLY the endpoints listed in [FOCUS_FUNCTIONS] for consistency with the spec and function requirements, and propose fixes where needed.

For EACH function name in [FOCUS_FUNCTIONS], you MUST return exactly one result:
- If the existing endpoint is acceptable, return an entry with ok=true and a brief description of why it is acceptable.
- If the endpoint needs a rewrite, return ok=false and include a clear description and the new implementation following the spec exactly.

Do not include any results for endpoints not listed in [FOCUS_FUNCTIONS].

Focus on the following items of clarification:
1. Ensure that code blocks do not ever invoke target APIs with invalid or spec-incompliant inputs.
2. Does the code block perform any dangerous casting? If so, fix it or stub out the functionality.
3. You may change the inputs/outputs of the code blocks to fix issues, but do not add or remove functions.
4. If you see any (REQ) comments that are incorrect, fix them!
5. If you see any (REQ-TODO) comments that you can address, address them!
6. If you see any stubbed out functions that you can implement, implement them!
7. If attributes are set but not checked (or vice versa), update the other code block to match. (i.e. a FUZZ_GET_ATTR_INT() call should have a corresponding FUZZ_SET_ATTR_INT() call in some other code block)
8. Generally, do not delete code unless it violates the specification and you can replace it with something that follows the specification.
9. Optimize for fuzzability and coverage of the target API.

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

[FOCUS_FUNCTIONS]
{focus_functions}
[/FOCUS_FUNCTIONS]
"""


class PolishDecision(BaseModel):
    name: str
    ok: bool
    description: str
    impl: Optional[FunctionImpl] = None


class PolishBatchResponse(BaseModel):
    results: List[PolishDecision]


def _apply_changes(current: List[FunctionImpl], changes: List[PolishChange]) -> List[FunctionImpl]:
    by_name = {e.name: e for e in current}
    for ch in changes:
        by_name[ch.name] = ch.impl
    return list(by_name.values())


MAX_REGEN_ATTEMPTS = 3


def run_polish_batch(model: str, info: ProjectInfo, functions: List[FunctionStub], objects: List[ObjectStub], all_endpoints: List[FunctionImpl], focus_endpoints: List[FunctionImpl], usage: UsageTracker, events: List[SynthesisEvent]) -> Tuple[List[PolishChange], List[FunctionImpl]]:
    """Run one polish batch for up to 20 focus endpoints.

    Returns (accepted_changes, updated_endpoints_full_set).
    """
    if not focus_endpoints:
        return [], all_endpoints

    llm = ChatOpenAI(model=model).with_structured_output(PolishBatchResponse, strict=True)

    focus_map: Dict[str, FunctionImpl] = {e.name: e for e in focus_endpoints}
    remaining: Dict[str, FunctionImpl] = dict(focus_map)
    accepted: List[PolishChange] = []

    for regen_attempt in range(MAX_REGEN_ATTEMPTS):
        prompt_vars = {
            'spec': SPEC,
            'functions': [f.model_dump_json() for f in functions],
            'objects': [o.model_dump_json() for o in objects],
            'inout_objects': [o.type for o in objects if len(o.direct_api_constructors) > 0],
            'endpoints': [e.model_dump_json() for e in all_endpoints],
            'focus_functions': list(remaining.keys()),
        }

        # Record attempt
        events.append(SynthesisEvent(
            event_type=EventType.POLISH_ATTEMPT,
            function_name='__batch__',
            attempt_number=regen_attempt + 1,
            prompt_template=PROMPT_POLISH_BATCH,
            prompt_variables=prompt_vars,
        ))

        with get_usage_metadata_callback() as cb:
            res: PolishBatchResponse = llm.invoke(PROMPT_POLISH_BATCH.format(**prompt_vars))
            usage.record('polish-batch', UsageMetrics.from_usage(cb.usage_metadata))

        # Consume decisions
        seen_names = set()
        for item in res.results:
            name = item.name
            if name not in remaining:
                continue
            seen_names.add(name)
            if item.ok:
                events.append(SynthesisEvent(
                    event_type=EventType.POLISH_NO_CHANGE,
                    function_name=name,
                    attempt_number=regen_attempt + 1,
                    error_message=item.description,
                ))
                remaining.pop(name, None)
            else:
                # Proposed change, validate
                if item.impl is None:
                    events.append(SynthesisEvent(
                        event_type=EventType.POLISH_NO_CHANGE,
                        function_name=name,
                        attempt_number=regen_attempt + 1,
                        error_message=f"No impl provided: {item.description}",
                    ))
                    remaining.pop(name, None)
                    continue
                results = validate_impl_build(info, item.impl, objects, events)
                if results and results[0].valid:
                    accepted.append(PolishChange(name=name, description=item.description, impl=item.impl))
                    events.append(SynthesisEvent(
                        event_type=EventType.POLISH_CHANGE,
                        function_name=name,
                        attempt_number=regen_attempt + 1,
                        cpp_code=item.impl.code,
                        error_message=item.description,
                    ))
                # Whether valid or not, we've made a determination; remove from remaining
                remaining.pop(name, None)

        # For any names not seen in this response, keep them in remaining for another attempt
        if not remaining:
            break

    # Apply accepted changes to the full set
    updated_endpoints = _apply_changes(all_endpoints, accepted) if accepted else all_endpoints
    return accepted, updated_endpoints


