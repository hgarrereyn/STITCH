from typing import List, Optional
import tempfile
import uuid

from ..data.metadata import UsageTracker, UsageMetrics, SchemaMetadata
from ..data.schema import Spec, Object, Endpoint, Variable, VariableDir, EndpointRole
from .data import FunctionStub, ObjectStub, FunctionImpl, InputOutput, SynthesisEvent, EventType
from ..build.validator import format_and_check
from ..build.build import build_full
from ..data.project_info import ProjectInfo
from ..utils.colors import Colors

from langchain_openai import ChatOpenAI
from langchain_core.callbacks import get_usage_metadata_callback
from pydantic import BaseModel

# How many times to regenerate a batch of endpoints if they are omitted from the output
MAX_REGEN_ATTEMPTS = 3

import os
SPEC = open(os.path.join(os.path.dirname(__file__), 'spec.md')).read()


PROMPT_SYNTH_BATCH = """
You are an expert C/C++ fuzzer engineer.

Your task is to generate code blocks that each invoke a particular function in a target API.
Each code block should be written in C++ and will be incorporated into larger test cases as part of a fuzzing campaign.

You will be given a list of functions ([FUNCTIONS]), a list of objects ([OBJECTS]), and a list of available input/output objects ([AVAILABLE_INOUT_OBJECTS]).

Generate a code block for each function in the [FUNCTIONS] section.

Utilize the following specification:
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
"""


PROMPT_SYNTH_BATCH_WITH_ERRORS = """
You are an expert C/C++ fuzzer engineer.

Your task is to generate code blocks that each invoke a particular function in a target API.
Each code block should be written in C++ and will be incorporated into larger test cases as part of a fuzzing campaign.

You will be given a list of functions ([FUNCTIONS]), a list of objects ([OBJECTS]), and a list of available input/output objects ([AVAILABLE_INOUT_OBJECTS]).
Generate a code block for each function in the [FUNCTIONS] section.

Your previous attempt to generate these code blocks is given in the [PREVIOUS_ATTEMPT] section along with the errors that were encountered.
Please fix those errors and generate the code blocks again.
You may change the inputs and outputs of the old code blocks to reflect changes in the code block template.

Utilize the following specification:
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

[PREVIOUS_ATTEMPT]
{previous_attempts}
[/PREVIOUS_ATTEMPT]
"""


class SynthBatchResponse(BaseModel):
    endpoints: List[FunctionImpl]


class ValidateImplResult(BaseModel):
    impl: FunctionImpl
    valid: bool
    error: Optional[str] = None


def run_synth_batch(model: str, batch: List[FunctionStub], objects: List[ObjectStub], usage: UsageTracker, events: List[SynthesisEvent] = None) -> List[FunctionImpl]:
    print('Running synth batch', [x.name for x in batch])

    if events is None:
        events = []

    # Keep track of remaining functions to synthesize
    batch_mapping = {f.name: f for f in batch}
    synthesized = []
    batch_id = str(uuid.uuid4())

    llm = ChatOpenAI(model=model).with_structured_output(SynthBatchResponse, strict=True)
    for regen_attempt in range(MAX_REGEN_ATTEMPTS):
        with get_usage_metadata_callback() as cb:
            print(f'Synthesizing batch {regen_attempt + 1} of {MAX_REGEN_ATTEMPTS}...')
            
            # Prepare prompt variables
            prompt_vars = {
                'spec': SPEC,
                'functions': [x.model_dump_json() for x in batch_mapping.values()],
                'objects': [o.model_dump_json() for o in objects],
                'inout_objects': [o.type for o in objects if len(o.direct_api_constructors) > 0]
            }
            
            # Record synthesis attempt for each function in the batch
            for func_name in batch_mapping.keys():
                events.append(SynthesisEvent(
                    event_type=EventType.SYNTHESIS_ATTEMPT,
                    function_name=func_name,
                    attempt_number=regen_attempt + 1,
                    batch_id=batch_id,
                    model_used=model,
                    prompt_template=PROMPT_SYNTH_BATCH,
                    prompt_variables=prompt_vars
                ))
            
            # Format the prompt
            formatted_prompt = PROMPT_SYNTH_BATCH.format(**prompt_vars)
            
            res: SynthBatchResponse = llm.invoke(formatted_prompt)
            usage.record('synth-endpoints (synth-batch)', UsageMetrics.from_usage(cb.usage_metadata))
            
            # Convert response to string for logging
            response_str = res.model_dump_json(indent=2) if res else "None"

            # Iterate over the synthesized endpoints and remove them from the batch mapping
            for endpoint in res.endpoints:
                if endpoint.name in batch_mapping:
                    batch_mapping.pop(endpoint.name)
                    synthesized.append(endpoint)
                    
                    # Record successful synthesis
                    events.append(SynthesisEvent(
                        event_type=EventType.SYNTHESIS_SUCCESS,
                        function_name=endpoint.name,
                        attempt_number=regen_attempt + 1,
                        batch_id=batch_id,
                        cpp_code=endpoint.code,
                        model_used=model,
                        tokens_used=cb.usage_metadata.get('total_tokens', 0) if cb.usage_metadata else None,
                        prompt_template=PROMPT_SYNTH_BATCH,
                        prompt_variables=prompt_vars,
                        llm_response=response_str
                    ))

            # Record failures for functions that weren't synthesized
            for func_name in batch_mapping.keys():
                events.append(SynthesisEvent(
                    event_type=EventType.SYNTHESIS_FAILURE,
                    function_name=func_name,
                    attempt_number=regen_attempt + 1,
                    batch_id=batch_id,
                    error_message="Function not included in LLM response",
                    model_used=model,
                    tokens_used=cb.usage_metadata.get('total_tokens', 0) if cb.usage_metadata else None,
                    prompt_template=PROMPT_SYNTH_BATCH,
                    prompt_variables=prompt_vars,
                    llm_response=response_str
                ))

            print(f'Synthesized {len(synthesized)} endpoints, {len(batch_mapping)} remaining')

            if len(batch_mapping) == 0:
                break

    return synthesized


def run_synth_batch_with_errors(model: str, previous_attempts: List[ValidateImplResult], batch: List[FunctionStub], objects: List[ObjectStub], usage: UsageTracker, events: List[SynthesisEvent] = None) -> List[FunctionImpl]:
    print('Running synth batch', [x.name for x in batch])

    if events is None:
        events = []

    # Keep track of remaining functions to synthesize
    batch_mapping = {f.name: f for f in batch}
    synthesized = []
    batch_id = str(uuid.uuid4())

    llm = ChatOpenAI(model=model).with_structured_output(SynthBatchResponse, strict=True)
    for regen_attempt in range(MAX_REGEN_ATTEMPTS):
        with get_usage_metadata_callback() as cb:
            print(f'Synthesizing batch {regen_attempt + 1} of {MAX_REGEN_ATTEMPTS}...')
            
            func_batch = [x for x in batch_mapping.values()]
            prev_attempt_batch = [x for x in previous_attempts if x.impl.name in batch_mapping]
            
            # Prepare prompt variables
            prompt_vars = {
                'spec': SPEC,
                'functions': [x.model_dump_json() for x in func_batch],
                'objects': [o.model_dump_json() for o in objects],
                'inout_objects': [o.type for o in objects if len(o.direct_api_constructors) > 0],
                'previous_attempts': [x.model_dump_json() for x in prev_attempt_batch]
            }
            
            # Record regeneration attempt for each function in the batch
            for func_name in batch_mapping.keys():
                events.append(SynthesisEvent(
                    event_type=EventType.REGENERATION_ATTEMPT,
                    function_name=func_name,
                    attempt_number=regen_attempt + 1,
                    batch_id=batch_id,
                    model_used=model,
                    prompt_template=PROMPT_SYNTH_BATCH_WITH_ERRORS,
                    prompt_variables=prompt_vars
                ))
            
            # Format the prompt
            formatted_prompt = PROMPT_SYNTH_BATCH_WITH_ERRORS.format(**prompt_vars)

            res: SynthBatchResponse = llm.invoke(formatted_prompt)
            usage.record('synth-endpoints (synth-batch)', UsageMetrics.from_usage(cb.usage_metadata))
            
            # Convert response to string for logging
            response_str = res.model_dump_json(indent=2) if res else "None"

            # Iterate over the synthesized endpoints and remove them from the batch mapping
            for endpoint in res.endpoints:
                if endpoint.name in batch_mapping:
                    batch_mapping.pop(endpoint.name)
                    synthesized.append(endpoint)
                    
                    # Record successful regeneration
                    events.append(SynthesisEvent(
                        event_type=EventType.SYNTHESIS_SUCCESS,
                        function_name=endpoint.name,
                        attempt_number=regen_attempt + 1,
                        batch_id=batch_id,
                        cpp_code=endpoint.code,
                        model_used=model,
                        tokens_used=cb.usage_metadata.get('total_tokens', 0) if cb.usage_metadata else None,
                        prompt_template=PROMPT_SYNTH_BATCH_WITH_ERRORS,
                        prompt_variables=prompt_vars,
                        llm_response=response_str
                    ))

            # Record failures for functions that weren't synthesized
            for func_name in batch_mapping.keys():
                events.append(SynthesisEvent(
                    event_type=EventType.SYNTHESIS_FAILURE,
                    function_name=func_name,
                    attempt_number=regen_attempt + 1,
                    batch_id=batch_id,
                    error_message="Function not included in LLM response",
                    model_used=model,
                    tokens_used=cb.usage_metadata.get('total_tokens', 0) if cb.usage_metadata else None,
                    prompt_template=PROMPT_SYNTH_BATCH_WITH_ERRORS,
                    prompt_variables=prompt_vars,
                    llm_response=response_str
                ))

            print(f'Synthesized {len(synthesized)} endpoints, {len(batch_mapping)} remaining')

            if len(batch_mapping) == 0:
                break

    return synthesized


def test_impl_in_schema(info: ProjectInfo, impl: FunctionImpl, objects: List[ObjectStub]) -> Optional[str]:
    schema = Spec(
        objects=[
            Object(name=o.type, attrs=[]) for o in objects if len(o.direct_api_constructors) > 0
        ] + [Object(name='void *', attrs=[])],
        endpoints=[impl.to_endpoint()]
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        f_meta = '%s/schema.json' % tmpdir
        with open(f_meta, 'w') as f:
            f.write(schema.model_dump_json())
        
        try:
            build_full(f_meta[:-5], info)
        except Exception as e:
            return str(e)
        
    return None


def validate_impl_build(info: ProjectInfo, impl: FunctionImpl, objects: List[ObjectStub], events: List[SynthesisEvent] = None) -> List[ValidateImplResult]:
    if events is None:
        events = []

    code = ''
    code += 'int main() {}\n'
    code += impl.to_function_proto()

    # Record validation attempt
    events.append(SynthesisEvent(
        event_type=EventType.VALIDATION_ATTEMPT,
        function_name=impl.name,
        attempt_number=1,
        cpp_code=code
    ))

    res = None

    # Convert input/output types to corresponding API managed objects (or fail if not possible)
    converted_inputs = []
    converted_outputs = []

    for inp in impl.inputs:
        # Find the object that matches the input type
        found = False
        for obj in objects:
            if inp.type in [obj.type] + obj.aliases:
                if len(obj.direct_api_constructors) > 0:
                    converted_inputs.append(InputOutput(name=inp.name, type=obj.type))
                    found = True
                    break
                else:
                    res = f'Input {inp.name} has type {inp.type} which is not a direct API constructable object type'
                    break

        if inp.type == 'void *':
            converted_inputs.append(InputOutput(name=inp.name, type=inp.type))
            found = True
        
        if not found:
            res = f'Input {inp.name} has type {inp.type} which is not an object type'
            break

    for out in impl.outputs:
        # Find the object that matches the output type
        found = False
        for obj in objects:
            if out.type in [obj.type] + obj.aliases:
                if len(obj.direct_api_constructors) > 0:
                    converted_outputs.append(InputOutput(name=out.name, type=obj.type))
                    found = True
                    break
                else:
                    res = f'Output {out.name} has type {out.type} which is not a direct API constructable object type'
                    break
                    
        if out.type == 'void *':
            converted_outputs.append(InputOutput(name=out.name, type=out.type))
            found = True
        
        if not found:
            res = f'Output {out.name} has type {out.type} which is not an object type'
            break

    if res is None:
        impl.inputs = converted_inputs
        impl.outputs = converted_outputs

    if res is None:
        # Checks with macros to allow cpp template assertions (e.g. no FUZZ_PARAM(void*))
        res = format_and_check(code, info, verbose=False, external=True)

    if res is None:
        # Second check with actual schema to catch any edge cases that seem to pass, e.g. fuzz_param in lambdas
        res = test_impl_in_schema(info, impl, objects)

    if res is not None:
        print(f'----[{Colors.RED}Invalid impl for {Colors.YELLOW}{impl.name}{Colors.END}:')
        print(impl.code)
        print('-' * 80)

        # Record validation failure
        events.append(SynthesisEvent(
            event_type=EventType.VALIDATION_FAILURE,
            function_name=impl.name,
            attempt_number=1,
            cpp_code=impl.code,
            validation_error=res
        ))
        
        return [
            ValidateImplResult(impl=impl, valid=False, error=res)
        ]
    else:
        print(f'-----[{Colors.GREEN}Valid impl for {Colors.YELLOW}{impl.name}{Colors.END}:')
        print(impl.code)
        print('-' * 80)
        
        # Record validation success
        events.append(SynthesisEvent(
            event_type=EventType.VALIDATION_SUCCESS,
            function_name=impl.name,
            attempt_number=1,
            cpp_code=impl.code
        ))
        
        return [
            ValidateImplResult(impl=impl, valid=True)
        ]
