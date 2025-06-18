from pydantic import BaseModel, Field
from typing import Optional, List, Dict


class BuildConfig(BaseModel):
    # -I<name>
    include_paths: List[str]

    # -L<name>
    lib_paths: List[str]

    # -l<name>
    libs: List[str]

    # <source_file>
    source_files: List[str]

    # extra flags
    flags: Optional[List[str]] = None


class CodegenHeader(BaseModel):
    name: str
    language: str


class CodegenDefine(BaseModel):
    name: str
    value: str


class CodegenConfig(BaseModel):
    headers: List[CodegenHeader]

    # Defines
    defines: Optional[List[CodegenDefine]] = None


class ProjectInfo(BaseModel):
    # Name of the project
    name: str

    # Path to codebase
    codebase: str

    # Build configuration
    build: BuildConfig

    # Codegen configuration
    codegen: CodegenConfig
