from pydantic import BaseModel, Field
from typing import List, Optional


class GhostAttribute(BaseModel):
    name: str = Field(description="The name of the ghost attribute")
    description: str = Field(description="A short description of the ghost attribute")
    type: str = Field(description="The type of the ghost attribute")
    possible_values: List[str] = Field(description="The possible values the ghost attribute can take")

class ObjectInfo(BaseModel):
    typ: str = Field(description="C/C++ type of the object")
    description: str = Field(description="A short description of the object")
    aliases: List[str] = Field(description="Other types that are aliases for this object")
    constructed_by: List[str] = Field(description="The functions that construct the object")
    destructed_by: List[str] = Field(description="The functions that destruct the object")
    possible_states: List[str] = Field(description="The possible states the object can be in")
    ghost_attributes: List[GhostAttribute] = Field(description="The ghost attributes of the object")


class FunctionInfo(BaseModel):
    name: str = Field(description="The name of the function")
    signature: str = Field(description="The signature of the function")
    description: str = Field(description="A short description of the function")
    usage_requirements: List[str] = Field(description="A list of requirements for using the function (e.g. API restrictions, etc.)")
    preconditions: List[str] = Field(description="A list of preconditions for the function")
    effects: List[str] = Field(description="A list of effects of the function")
    imposes: List[str] = Field(description="A list of imposes of the function") 

class SchemaMetadata(BaseModel):
    """Metadata about the schema"""
    objects: Optional[List[ObjectInfo]] = None
    functions: Optional[List[FunctionInfo]] = None
