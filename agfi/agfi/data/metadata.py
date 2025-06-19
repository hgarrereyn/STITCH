from pydantic import BaseModel, Field
from typing import List, Optional, Any, Dict
from enum import Enum

from langchain_core.messages.ai import UsageMetadata

from ..utils.colors import Colors


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

# (input/output) price ($) per million tokens
MODEL_COST = {
    'gpt-4.1-2025-04-14': (2.0, 8.0),
}

class UsageTokens(BaseModel):
    input_tokens: int = Field(description="The number of tokens used for the input", default=0)
    output_tokens: int = Field(description="The number of tokens used for the output", default=0)
    total_tokens: int = Field(description="The total number of tokens used", default=0)

    def add(self, other: 'UsageTokens'):
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.total_tokens += other.total_tokens

    @staticmethod
    def from_usage(usage: UsageMetadata):
        return UsageTokens(
            input_tokens=usage['input_tokens'],
            output_tokens=usage['output_tokens'],
            total_tokens=usage['total_tokens'],
        )
    
    def cost(self, input_price: float, output_price: float) -> float:
        return (self.input_tokens / 1_000_000) * input_price + (self.output_tokens / 1_000_000) * output_price

class UsageMetrics(BaseModel):
    models: Dict[str, UsageTokens] = Field(description="The number of tokens used for each model", default={})

    def add(self, other: 'UsageMetrics'):
        for model, tokens in other.models.items():
            if model not in self.models:
                self.models[model] = UsageTokens()
            self.models[model].add(tokens)

    @staticmethod
    def from_usage(usage: Dict[str, UsageMetadata]):
        metrics = UsageMetrics()
        for model, usage_metadata in usage.items():
            metrics.models[model] = UsageTokens.from_usage(usage_metadata)
        return metrics
    
    def cost(self) -> float:
        cost = 0.0
        for model, tokens in self.models.items():
            if model not in MODEL_COST:
                print(f'[{Colors.RED}-{Colors.END}] Unknown model {model}')
                continue
            cost += tokens.cost(MODEL_COST[model][0], MODEL_COST[model][1])
        return cost
    
class UsageTracker(BaseModel):
    by_task: Dict[str, UsageMetrics] = Field(description="The number of tokens used for each task", default={})

    def record(self, task: str, usage: UsageMetrics):
        if task not in self.by_task:
            self.by_task[task] = UsageMetrics()
        self.by_task[task].add(usage)

    def cost_by_task(self) -> Dict[str, float]:
        return {task: metrics.cost() for task, metrics in self.by_task.items()}

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

    # Token usage
    usage: UsageTracker = Field(description="The number of tokens used for each task", default=UsageTracker())
