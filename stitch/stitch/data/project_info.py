from pydantic import BaseModel, Field
from typing import List, Literal


class CodegenHeader(BaseModel):
    path: str
    language: Literal["c", "cpp"]


class CodegenDefine(BaseModel):
    name: str
    value: str


class CodegenConfig(BaseModel):
    headers: List[CodegenHeader] = Field(default_factory=list)
    defines: List[CodegenDefine] = Field(default_factory=list)


class ProjectInfo(BaseModel):
    build_flags: List[str] = Field(default_factory=list)
    codegen: CodegenConfig = Field(default_factory=CodegenConfig)
