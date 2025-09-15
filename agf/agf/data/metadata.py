from typing import Dict, List
from pydantic import BaseModel, Field
from langchain_core.messages.ai import UsageMetadata

from ..utils.colors import Colors


# (input/output) price ($) per million tokens
MODEL_COST = {
    'gpt-4.1-2025-04-14': (2.0, 8.0),
    'gpt-5-2025-08-07': (1.25, 10.0),
    'gpt-5': (1.25, 10.0),
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
