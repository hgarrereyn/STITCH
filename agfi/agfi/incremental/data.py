from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime
from enum import Enum

from ..data.schema import Spec, Object, Attr, Endpoint, OutlivesConstraint, EndpointRole, VariableDir, Variable
from ..data.metadata import UsageTracker


class EventType(str, Enum):
    SYNTHESIS_ATTEMPT = "synthesis_attempt"
    SYNTHESIS_SUCCESS = "synthesis_success"
    SYNTHESIS_FAILURE = "synthesis_failure"
    VALIDATION_ATTEMPT = "validation_attempt"
    VALIDATION_SUCCESS = "validation_success"
    VALIDATION_FAILURE = "validation_failure"
    REGENERATION_ATTEMPT = "regeneration_attempt"
    POLISH_ATTEMPT = "polish_attempt"
    POLISH_CHANGE = "polish_change"
    POLISH_NO_CHANGE = "polish_no_change"


class SynthesisEvent(BaseModel):
    event_type: EventType
    timestamp: datetime = Field(default_factory=datetime.now)
    function_name: str
    attempt_number: int
    batch_id: Optional[str] = None
    cpp_code: Optional[str] = None
    error_message: Optional[str] = None
    validation_error: Optional[str] = None
    model_used: Optional[str] = None
    tokens_used: Optional[int] = None
    # Prompt and response logging
    prompt_template: Optional[str] = None
    prompt_variables: Optional[Dict[str, Any]] = None
    llm_response: Optional[str] = None


class ObjectStub(BaseModel):
    type: str = Field(description="Fully-qualified C/C++ type of the object")
    aliases: List[str] = Field(description="Other types that are aliases for this object")
    description: str = Field(description="A detailed description of the object")
    definition: str = Field(description="The code definition of the object")
    direct_api_constructors: List[str] = Field(description="List of functions that directly construct this object -- do not include functions that indirectly construct this object (e.g. functions that construct a parent object that contains this one)")
    owned_by: List[str] = Field(description="List of other objects that own this object (i.e. if the object is a managed field in some parent object)")


class FunctionEntry(BaseModel):
    header: str
    name: str


class FunctionEntryBatch(BaseModel):
    header: str
    header_code: str
    entries: List[FunctionEntry]


class FunctionStub(BaseModel):
    name: str = Field(description="The name of the function")
    signature: str = Field(description="The signature of the function")
    description: str = Field(description="A detailed description of the function")
    requirements: List[str] = Field(description="A list of user-facing requirements for the function")


class InputOutput(BaseModel):
    name: str = Field(description="The name of the input or output")
    type: str = Field(description="The type of the input or output")


class FunctionImpl(BaseModel):
    name: str = Field(description="The name of the function")
    code: str = Field(description="The code implementation for the function")
    inputs: List[InputOutput] = Field(description="The inputs accepted by the code block")
    outputs: List[InputOutput] = Field(description="The outputs produced by the code block")

    def to_function_proto(self) -> str:
        args = []
        for inp in self.inputs:
            args.append(f'{inp.type} {inp.name}')

        code = ''
        code += 'void test_function('
        code += ', '.join(args)
        code += ') {\n'
        code += self.code
        code += '\n}\n'
        return code
    
    def to_endpoint(self) -> Endpoint:
        vars = []

        inp_name = set([x.name for x in self.inputs])
        out_name = set([x.name for x in self.outputs])

        for inp in self.inputs:
            if inp.name not in out_name:
                vars.append(Variable(name=inp.name, type=inp.type, dir=VariableDir.Del))
            else:
                vars.append(Variable(name=inp.name, type=inp.type, dir=VariableDir.InOut))

        for out in self.outputs:
            if out.name not in inp_name:
                vars.append(Variable(name=out.name, type=out.type, dir=VariableDir.New))

        return Endpoint(
            name=self.name,
            variables=vars,
            template=self.code,
            hints=[],
            preconditions=[],
            effects=[],
            role=EndpointRole.NONE,
            outlives=[],
        )


class FailedFunctionImpl(BaseModel):
    name: str
    impl: FunctionImpl
    error: str


class FunctionHints(BaseModel):
    name: str = Field(description="The name of the endpoint")
    hints: List[str] = Field(description="A list of (hex-encoded) hints for the buffers/files used in the endpoint template")


class ProjectMetadata(BaseModel):
    objects: Optional[List[ObjectStub]] = None
    functions: Optional[List[FunctionStub]] = None

    endpoints: Optional[List[FunctionImpl]] = None

    validated_endpoints: Optional[List[FunctionImpl]] = None
    failed_endpoints: Optional[List[FailedFunctionImpl]] = None

    endpoint_hints: Optional[List[FunctionHints]] = None

    usage: UsageTracker = Field(description="The number of tokens used for each task", default=UsageTracker())

    events: List[SynthesisEvent] = Field(description="Events tracking synthesis attempts and results", default=[])

    metadata: Dict[str, Any] = Field(description="Additional metadata", default={})

    # Optional: record polish changes applied in the current run
    polish_changes: Optional[List["PolishChange"]] = None

    def to_schema(self) -> Spec:
        schema = Spec(
            objects=[],
            endpoints=[],
        )

        for obj in self.objects:
            schema.objects.append(Object(name=obj.type, attrs=[]))

        schema.objects.append(Object(name='void *', attrs=[]))

        # Build a lookup for hints by endpoint name
        hints_map = {h.name: h.hints for h in (self.endpoint_hints or [])}

        for func in self.validated_endpoints:
            ep = func.to_endpoint()
            if ep.name in hints_map:
                ep.hints = hints_map[ep.name]
            schema.endpoints.append(ep)

        return schema


class PolishChange(BaseModel):
    name: str = Field(description="The name of the endpoint that was changed")
    description: str = Field(description="Summary of why the change was needed and what changed")
    impl: FunctionImpl = Field(description="The updated implementation for the endpoint")
