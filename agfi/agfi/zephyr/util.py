from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from typing import Any, List

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
