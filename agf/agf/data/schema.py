from pydantic import BaseModel
from typing import List, Literal


class Variable(BaseModel):
    name: str
    typ: str
    dir: Literal['new', 'del', '*']


class Endpoint(BaseModel):
    name: str
    variables: List[Variable]
    template: str


class Schema(BaseModel):
    """Fuzzer schema containing all information needed to fuzz."""
    endpoints: List[Endpoint]
