from typing import Dict, List
from pydantic import BaseModel, Field
from langchain_core.messages.ai import UsageMetadata
from openai.types.responses.response_usage import ResponseUsage

from ..utils.colors import Colors


# (input/output) price ($) per million tokens
MODEL_COST = {
    # GPT-5.2
    'gpt-5.2': (1.75, 14.0),
    'gpt-5.2-chat-latest': (1.75, 14.0),
    'gpt-5.2-codex': (1.75, 14.0),
    'gpt-5.2-pro': (21.0, 168.0),
    # GPT-5.1
    'gpt-5.1': (1.25, 10.0),
    'gpt-5.1-chat-latest': (1.25, 10.0),
    'gpt-5.1-codex-max': (1.25, 10.0),
    'gpt-5.1-codex': (1.25, 10.0),
    # GPT-5
    'gpt-5': (1.25, 10.0),
    'gpt-5-chat-latest': (1.25, 10.0),
    'gpt-5-codex': (1.25, 10.0),
    'gpt-5-pro': (15.0, 120.0),
    'gpt-5-mini': (0.25, 2.0),
    'gpt-5-nano': (0.05, 0.4),
    # GPT-4.1
    'gpt-4.1': (2.0, 8.0),
    'gpt-4.1-mini': (0.4, 1.6),
    'gpt-4.1-nano': (0.1, 0.4),
    # GPT-4o
    'gpt-4o': (2.5, 10.0),
    'gpt-4o-2024-05-13': (5.0, 15.0),
    'gpt-4o-mini': (0.15, 0.6),
}


def _lookup_model_cost(model: str):
    """Look up cost for a model, falling back to prefix matching for dated variants."""
    if model in MODEL_COST:
        return MODEL_COST[model]
    # Try prefix matching: e.g. 'gpt-5-2026-01-01' -> 'gpt-5'
    for known in sorted(MODEL_COST, key=len, reverse=True):
        if model.startswith(known):
            return MODEL_COST[known]
    return None

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

    @staticmethod
    def from_openai(usage: ResponseUsage):
        return UsageTokens(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
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
            prices = _lookup_model_cost(model)
            if prices is None:
                print(f'[{Colors.RED}-{Colors.END}] Unknown model {model}')
                continue
            cost += tokens.cost(prices[0], prices[1])
        return cost
    
class UsageTracker(BaseModel):
    by_task: Dict[str, UsageMetrics] = Field(description="The number of tokens used for each task", default={})

    def record(self, task: str, usage: UsageMetrics):
        if task not in self.by_task:
            self.by_task[task] = UsageMetrics()
        self.by_task[task].add(usage)

    def record_model(self, task: str, model: str, usage: UsageTokens):
        if task not in self.by_task:
            self.by_task[task] = UsageMetrics()
        if model not in self.by_task[task].models:
            self.by_task[task].models[model] = UsageTokens()
        self.by_task[task].models[model].add(usage)

    def cost_by_task(self) -> Dict[str, float]:
        return {task: metrics.cost() for task, metrics in self.by_task.items()}
