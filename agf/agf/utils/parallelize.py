from typing import Callable, List, Any
from concurrent.futures import ThreadPoolExecutor


def parallelize(func: Callable, args: List[Any], num_workers: int = 10) -> List[Any]:
    with ThreadPoolExecutor(num_workers) as p:
        return list(p.map(func, args))

