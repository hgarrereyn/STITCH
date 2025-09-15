from pydantic import BaseModel, Field
from typing import List, Optional
from ..data.schema import Endpoint, Variable, VariableDir, Spec, Object
from ..data.metadata import UsageTracker


class ObjectInfo(BaseModel):
    ptr_type: str = Field(description="pointer type of the object")
    aliases: List[str] = Field(description="Other types that are aliases for this object")
    definition: str = Field(description="The code definition of the object")
    description: str = Field(description="A detailed description of the object")
    direct_api_constructors: List[str] = Field(description="List of functions that directly construct this object -- do not include functions that indirectly construct this object (e.g. functions that construct a parent object that contains this one)")
    owned_by: List[str] = Field(description="List of other objects that own this object (i.e. if the object is a managed field in some parent object)")


class FunctionInfo(BaseModel):
    name: str = Field(description="The name of the function")
    signature: str = Field(description="The signature of the function")
    description: str = Field(description="A detailed description of the function")
    requirements: List[str] = Field(description="A list of requirements for the function")
    error: Optional[str] = Field(description="An error message if the function is invalid")


class VarArg(BaseModel):
    name: str = Field(description="The name of the variable")
    type: str = Field(description="The type of the variable")


class FunctionImpl(BaseModel):
    name: str = Field(description="The name of the function")
    inputs: List[VarArg] = Field(description="The inputs accepted by the code block")
    outputs: List[VarArg] = Field(description="The outputs produced by the code block")
    code: str = Field(description="The code implementation for the function")

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
            hints=[]
        )


class FunctionImplError(BaseModel):
    stub: FunctionImpl
    type: str = Field(description="The type of the error")
    error: str = Field(description="An error message if the function implementation is invalid")


class ProjectMetadata(BaseModel):
    objects: Optional[List[ObjectInfo]] = None
    functions: Optional[List[FunctionInfo]] = None

    endpoints: Optional[List[FunctionImpl]] = None

    usage: UsageTracker = Field(description="The number of tokens used for each task", default=UsageTracker())

    def to_schema(self) -> Spec:
        schema = Spec(
            objects=[],
            endpoints=[],
        )

        for obj in self.objects:
            schema.objects.append(Object(name=obj.ptr_type, attrs=[]))

        schema.objects.append(Object(name='void *', attrs=[]))

        # Build a lookup for hints by endpoint name
        # hints_map = {h.name: h.hints for h in (self.endpoint_hints or [])}

        for func in self.endpoints:
            ep = func.to_endpoint()
            # if ep.name in hints_map:
            #     ep.hints = hints_map[ep.name]
            schema.endpoints.append(ep)

        return schema
