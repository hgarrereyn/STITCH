from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from typing import Any, List, Optional
import tempfile
import importlib.util
import os

from ..utils.colors import Colors


def smash_fn(args) -> Any:
    fn = args[0]
    return fn(*args[1])


def parallel_run(threads: int, func: Any, inputs: List[Any]) -> List[Any]:
    with ThreadPoolExecutor(max_workers=threads) as executor:
        res = list(executor.map(smash_fn, [(func, i) for i in inputs]))

    # Flatten the list
    return [item for sublist in res for item in sublist]


def log(mode: str, msg: str):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    clr = Colors.GREEN if mode == 'VALID' else Colors.RED if mode == 'ERROR' else Colors.YELLOW
    print(f"[{now}] [{Colors.BOLD}{clr}{mode}{Colors.END}] {msg}")


def sample_generator(generator: str, n: int = 1000) -> List[bytes]:
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
                traceback = traceback.format_exc()
                raise Exception(f"Generator crashed at sample {i}: {e}\n{traceback}")
        
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
        
        # Check for uniqueness - at least 80% of outputs should be different
        unique_outputs = set(outputs)
        if len(unique_outputs) < 0.8 * len(outputs):
            print(f"Generator produced duplicate outputs in 1000 iterations. Expected at least {0.8 * len(outputs)} unique outputs, got {len(unique_outputs)}")
            return f'Generator produced duplicate outputs in 1000 iterations. Expected at least {0.8 * len(outputs)} unique outputs, got {len(unique_outputs)}'

        # If we get here, all tests passed
        return None
                
    except Exception as e:
        print(f"Error validating generator: {e}")
        return f'Error validating generator: {e}'
