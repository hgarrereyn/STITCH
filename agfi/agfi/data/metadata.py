from pydantic import BaseModel, Field
from typing import List, Optional
from enum import Enum


class ObjectLifetime(str, Enum):
    """
    Enum representing the lifetime of an object.
    """
    PERSISTENT = "persistent"
    EPHEMERAL = "ephemeral"

class ObjectStub(BaseModel):
    type: str = Field(description="Fully-qualified C/C++ type of the object")
    aliases: List[str] = Field(description="Other types that are aliases for this object")
    description: str = Field(description="A detailed description of the object")
    lifetime: ObjectLifetime = Field(description="Whether the object is persistent or ephemeral")
    definition: str = Field(description="The code definition of the object")

class FunctionStub(BaseModel):
    name: str = Field(description="The name of the function")
    signature: str = Field(description="The signature of the function")
    description: str = Field(description="A detailed description of the function")
    requirements: List[str] = Field(description="A list of requirements for the function")

class FunctionArgumentUsage(str, Enum):
    """
    Enum representing how a function argument is used.
    
    Attributes:
        DEFAULT: The argument is used as an input and is available afterwards.
        NEW: The argument is newly created or initialized by this function.
        DELETE: The argument is deleted or freed by this function.
    """
    DEFAULT = "default"
    NEW = "new"
    DELETE = "delete"

class FunctionArgument(BaseModel):
    name: str = Field(description="The name of the argument")
    sig_type: str = Field(description="The type of the argument as it appears in the signature")
    canonical_type: str = Field(description="The matching base type (one of the provided object types), or none")
    usage: FunctionArgumentUsage = Field(description="Whether the argument is required, optional, or default")

class FunctionStubMapping(BaseModel):
    name: str = Field(description="The name of the function")
    arguments: List[FunctionArgument] = Field(description="The arguments of the function")
    return_value: FunctionArgument = Field(description="The return type of the function")

class FunctionStubTemplate(BaseModel):
    name: str = Field(description="The name of the function")
    stub: str = Field(description="The C++ stub for the function (following the guidelines provided)")

class FunctionStubHints(BaseModel):
    name: str = Field(description="The name of the function")
    hints: List[str] = Field(description="A list of (hex-encoded) hints for the buffers/files used in the function")

class SchemaMetadata(BaseModel):
    """Metadata about the schema"""
    # Initial flow
    objects: Optional[List[ObjectStub]] = None
    functions: Optional[List[FunctionStub]] = None

    # Mapping flow
    mapping_functions: Optional[List[FunctionStubMapping]] = None

    # Template flow
    template_functions: Optional[List[FunctionStubTemplate]] = None

    # Hints flow
    hints_functions: Optional[List[FunctionStubHints]] = None
