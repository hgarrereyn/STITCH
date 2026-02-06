from pydantic import BaseModel, Field
from typing import List
from enum import Enum


class Spec(BaseModel):
    objects: List['Object']
    endpoints: List['Endpoint']


class Object(BaseModel):
    name: str


class VariableDir(str, Enum):
    InOut = '*'
    New = 'new'
    Del = 'del'


class Variable(BaseModel):
    name: str
    type: str
    dir: VariableDir = Field(default=VariableDir.InOut)


class Endpoint(BaseModel):
    name: str
    variables: List[Variable] = Field(default_factory=list)
    template: str
    hints: List[str] = Field(default_factory=list)
    # High-level data type classifications for this endpoint's fuzzed data,
    # as produced by the Zephyr classifiers. This is propagated into the
    # Rust `Spec` / `PSpec` so that mutators can reason about compatible
    # endpoints when performing cross-pollination.
    data_types: List[str] = Field(default_factory=list)


# allow forward refs
Spec.model_rebuild()
Object.model_rebuild()
Endpoint.model_rebuild()
