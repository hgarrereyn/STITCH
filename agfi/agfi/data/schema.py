from pydantic import BaseModel, Field
from typing import List, Literal, Union
from enum import Enum


class Spec(BaseModel):
    objects: List['Object']
    endpoints: List['Endpoint']


class Object(BaseModel):
    name: str
    # attrs: List['Attr'] = Field(default_factory=list)


# class IntAttrType(BaseModel):
#     type: Literal['int'] = Field('int', alias='type')


# class EnumAttrType(BaseModel):
#     type: Literal['enum'] = Field('enum', alias='type')
#     variants: List[str]


# Discriminated union on 'type'
# AttrType = Union[IntAttrType, EnumAttrType]


# class Attr(BaseModel):
#     name: str
#     attr_type: AttrType = Field(..., discriminator='type')


class VariableDir(str, Enum):
    InOut = '*'
    New = 'new'
    Del = 'del'


class Variable(BaseModel):
    name: str
    type: str
    dir: VariableDir = Field(default=VariableDir.InOut)


# class EndpointRole(str, Enum):
#     NONE = 'none'
#     LIBRARY_INITIALIZER = 'library_initializer'


# class OutlivesConstraint(BaseModel):
#     a: str
#     b: str


class Endpoint(BaseModel):
    name: str
    variables: List[Variable] = Field(default_factory=list)
    template: str
    hints: List[str] = Field(default_factory=list)
    # preconditions: List[str] = Field(default_factory=list)
    # effects: List[str] = Field(default_factory=list)
    # role: EndpointRole = Field(default=EndpointRole.NONE)
    # outlives: List[OutlivesConstraint] = Field(default_factory=list)


# allow forward refs
Spec.model_rebuild()
Object.model_rebuild()
# Attr.model_rebuild()
Endpoint.model_rebuild()
