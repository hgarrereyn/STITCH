from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from ..data.schema import Endpoint, Variable, VariableDir, Spec, Object
from ..data.metadata import UsageTracker
from .util import sample_generator


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


class UnharnessableFunction(BaseModel):
    """
    A function that the model has determined cannot be safely or meaningfully
    harnessed under the current constraints.
    """
    name: str = Field(description="The name of the function that is not harnessable")
    reason: str = Field(description="One-sentence explanation of why this function cannot be harnessed")


class FunctionClassifier(BaseModel):
    name: str
    data_types: List[str]


class DataTypeInfo(BaseModel):
    data_type: str
    description: str


class DataGenerator(BaseModel):
    data_type: str
    generator: str


class ProjectMetadata(BaseModel):
    objects: Optional[List[ObjectInfo]] = None
    functions: Optional[List[FunctionInfo]] = None

    endpoints: Optional[List[FunctionImpl]] = None

    # Functions that were explicitly marked as unharnessable by the model
    unharnessable_functions: Optional[List[UnharnessableFunction]] = None

    # Types of data used by the endpoints
    endpoint_classifiers: Optional[List[FunctionClassifier]] = None

    # Generators for each type of data
    data_types: Optional[List[DataTypeInfo]] = None
    generators: Optional[List[DataGenerator]] = None

    usage: UsageTracker = Field(description="The number of tokens used for each task", default=UsageTracker())

    metadata: Dict[str, Any] = Field(description="Additional metadata", default={})

    def to_schema(self, hint_synthesis: int = 1000) -> Spec:
        schema = Spec(
            objects=[],
            endpoints=[],
        )

        for obj in self.objects:
            schema.objects.append(Object(name=obj.ptr_type, attrs=[]))

        schema.objects.append(Object(name='void *', attrs=[]))

        # Run each generator hint_synthesis times
        synthesized_data = {}
        if self.generators is not None:
            for generator in self.generators:
                hints = sample_generator(generator.generator, n=hint_synthesis, allow_errors=True)
                hints =  list(set(hints))
                synthesized_data[generator.data_type] = [x.hex() for x in hints]

        classifiers_by_name = {}
        if self.endpoint_classifiers is not None:
            classifiers_by_name = {c.name: c for c in self.endpoint_classifiers}

        for func in self.endpoints:
            ep = func.to_endpoint()

            if func.name in classifiers_by_name:
                classifier = classifiers_by_name[func.name]

                # Attach synthesized hints for each classified data type.
                for data_type in classifier.data_types:
                    if data_type in synthesized_data:
                        ep.hints.extend(synthesized_data[data_type])

                # Propagate the high-level data type tags so the Rust-side
                # spec (and ultimately the mutators) can perform
                # cross-pollination between endpoints using the same kind of
                # data.
                ep.data_types = list(classifier.data_types)

            schema.endpoints.append(ep)

        return schema


class ProjectSynthMetadata(BaseModel):
    endpoint_failures: List[FunctionImplError]
