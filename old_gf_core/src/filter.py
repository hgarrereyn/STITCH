from typing import List, Dict
from dataclasses import dataclass

@dataclass
class Variable:
    type: str
    id: int

@dataclass
class Endpoint:
    name: str
    variables: Dict[str, Variable]

@dataclass
class Testcase:
    endpoints: List[Endpoint]