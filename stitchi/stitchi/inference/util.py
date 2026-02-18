from __future__ import annotations

from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from typing import Any, List, Optional, Callable, Type, TypeVar, TYPE_CHECKING
import tempfile
import importlib.util
import os
import traceback

from openai import OpenAI
import tiktoken

from ..utils.colors import Colors
from ..data.metadata import UsageTracker, UsageTokens

if TYPE_CHECKING:
    from .ui import TokenProgress

DEFAULT_MODEL = 'gpt-5'


def _get_encoder(model: str) -> tiktoken.Encoding:
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        return tiktoken.get_encoding("o200k_base")

_client: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI()
    return _client


def get_model() -> str:
    """
    Get the LLM model to use from the STITCH_MODEL environment variable.
    Falls back to DEFAULT_MODEL if not set.
    """
    return os.environ.get('STITCH_MODEL', DEFAULT_MODEL)


T = TypeVar("T")


def call_llm(
    *,
    input: list,
    text_format: Type[T],
    usage: UsageTracker,
    task: str,
    model: Optional[str] = None,
    reasoning: Optional[dict] = None,
    token_progress: Optional[TokenProgress] = None,
) -> T:
    """Stream an OpenAI Responses API call and return the parsed output.

    Updates *token_progress* in real time so the spinner can display
    input/output token counts as they accumulate.
    """
    if model is None:
        model = get_model()

    client = _get_client()

    kwargs: dict[str, Any] = dict(
        model=model,
        input=input,
        text_format=text_format,
    )
    if reasoning is not None:
        kwargs["reasoning"] = reasoning

    rough_input = 0
    rough_output = 0

    if token_progress is not None:
        enc = _get_encoder(model)
        rough_input = sum(len(enc.encode(msg.get("content", ""))) for msg in input if isinstance(msg, dict))
        token_progress.input_tokens += rough_input

    with client.responses.stream(**kwargs) as stream:
        for event in stream:
            if token_progress is not None and hasattr(event, "delta") and isinstance(getattr(event, "delta", None), str):
                increment = max(1, len(event.delta) // 4)
                rough_output += increment
                token_progress.output_tokens += increment

        response = stream.get_final_response()

    if token_progress is not None:
        token_progress.input_tokens += response.usage.input_tokens - rough_input
        token_progress.output_tokens += response.usage.output_tokens - rough_output
        token_progress.calls += 1

    usage.record_model(task, model, UsageTokens.from_openai(response.usage))
    return response.output_parsed


def smash_fn(args) -> Any:
    fn = args[0]
    return fn(*args[1])


def parallel_run(threads: int, func: Any, inputs: List[Any]) -> List[Any]:
    with ThreadPoolExecutor(max_workers=threads) as executor:
        res = list(executor.map(smash_fn, [(func, i) for i in inputs]))

    # Flatten the list
    return [item for sublist in res for item in sublist]

def parallel_run_raw(threads: int, func: Any, inputs: List[Any]) -> List[Any]:
    with ThreadPoolExecutor(max_workers=threads) as executor:
        res = list(executor.map(smash_fn, [(func, i) for i in inputs]))
    return res

def log(mode: str, msg: str):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    clr = Colors.GREEN if mode == 'VALID' else Colors.RED if mode == 'ERROR' else Colors.YELLOW
    print(f"[{now}] [{Colors.BOLD}{clr}{mode}{Colors.END}] {msg}")


def sample_generator(generator: str, n: int = 1000, allow_errors: bool = False) -> List[bytes]:
    """
    Sample a valid generator N times and return the outputs.
    
    Args:
        generator: Python code as a string containing a 'generator' function
        n: Number of samples to generate (default: 1000)
        
    Returns:
        List of generator outputs
        
    Raises:
        Exception: If the generator is invalid or fails during execution
    """
    # Create a temporary file to write the generator code
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(generator)
        temp_file_path = f.name
    
    try:
        # Load the module from the temporary file
        spec = importlib.util.spec_from_file_location("temp_generator", temp_file_path)
        if spec is None or spec.loader is None:
            raise Exception("Failed to load the module")
            
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        
        # Get the generator function
        if not hasattr(module, 'generate'):
            raise Exception("Generator function not found")
            
        generator_func = getattr(module, 'generate')
        if not callable(generator_func):
            raise Exception("Generator function is not callable")
        
        # Sample the generator N times
        outputs = []
        for i in range(n):
            try:
                output = generator_func(seed=i)
                outputs.append(output)
            except Exception as e:
                if allow_errors:
                    continue
                raise Exception(f"Generator crashed at sample {i}: {e}\n{traceback.format_exc()}")
        
        return outputs
        
    finally:
        # Clean up the temporary file
        try:
            os.unlink(temp_file_path)
        except OSError:
            pass  # File might already be deleted


def validate_generator(generator: str) -> Optional[str]:
    """
    Validate a generator by loading it as a Python module and testing it.
    
    Args:
        generator: Python code as a string containing a 'generator' function
        
    Returns:
        None if the generator passes all tests (uniqueness and stability), error message otherwise
    """
    try:
        # Use sample_generator to get outputs and catch any errors
        outputs = sample_generator(generator, n=1000)
        
        # Check for uniqueness - at least 10% of outputs should be different
        unique_outputs = set(outputs)
        if len(unique_outputs) < 0.1 * len(outputs):
            return f'Generator produced duplicate outputs in 1000 iterations. Expected at least {0.1 * len(outputs)} unique outputs, got {len(unique_outputs)}'

        return None
                
    except Exception as e:
        return f'Error validating generator: {e}'
