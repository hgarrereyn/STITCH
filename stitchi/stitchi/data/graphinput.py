from pydantic import BaseModel
from typing import List


class NodeRef(BaseModel):
    node_idx: int
    conn_idx: int

class Node(BaseModel):
    index: int
    typ: int
    layer: int
    in_ref: List[NodeRef]
    out_ref: List[NodeRef]
    context: List[int]

class GraphInput(BaseModel):
    graph: List[Node]
