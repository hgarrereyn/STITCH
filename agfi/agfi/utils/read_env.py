import os
from pathlib import Path
from typing import List

ENV_PATH = os.path.join(os.path.dirname(__file__), '..', '..', '.env')

def read_env_file() -> dict:
    env_path = Path(ENV_PATH)
    if not env_path.exists():
        return {}
    
    env_vars = {}
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                key, value = line.split('=', 1)
                env_vars[key.strip()] = value.strip().strip("'").strip('"')
    return env_vars
