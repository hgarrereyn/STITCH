from pydantic import BaseModel, Field
from typing import List, Literal


class CodegenHeader(BaseModel):
    path: str
    language: Literal['c', 'cpp']


class CodegenScanOnlyHeader(BaseModel):
    path: str


class CodegenDefine(BaseModel):
    name: str
    value: str


class CodegenConfig(BaseModel):
    # Target headers
    headers: List[CodegenHeader] = Field(default_factory=list)

    # Extra headers used only for API scanning/inference.
    scan_only_headers: List[CodegenScanOnlyHeader] = Field(default_factory=list)

    # Extra defines
    defines: List[CodegenDefine] = Field(default_factory=list)


class ProjectInfo(BaseModel):
    # Build configuration
    build_flags: List[str] = Field(default_factory=list)

    # Codegen configuration
    codegen: CodegenConfig = Field(default_factory=CodegenConfig)
