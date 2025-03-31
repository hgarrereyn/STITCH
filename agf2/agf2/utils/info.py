from pydantic import BaseModel


class ProjectInfo(BaseModel):
    # Name of the project
    name: str

    # Path to gf_synth config (i.e. included headers)
    config: str

    build_script_full: str
    build_script: str
    build_script_ext: str

    # Path to codebase
    codebase: str

    # Path to RAG for the project
    rag: str
