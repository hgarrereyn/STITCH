import hashlib

from pydantic import BaseModel


def summary_hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:8]


class Crash(BaseModel):
    """Represents a crashing testcase"""
    # Crash graph data (json)
    graph_data: str

    # Associated fuzzer binary
    fuzzer_path: str

    # Crash report (json)
    crash_report: str

    # Crash summary
    crash_summary: str


class ReproducedCrash(BaseModel):
    """Represents a crash that has an external reproducer"""
    # Minimized graph data (json)
    minimized_graph_data: str

    # Associated fuzzer binary
    fuzzer_path: str

    # External reproducer code (cpp)
    external_code: str

    # Crash report (json)
    crash_report: str

    # Crash summary
    crash_summary: str


class MinimizedCrash(BaseModel):
    """Represents a crash that has been LLM-minimized"""
    # Minimized code (cpp)
    minimized_code: str

    # Minimized report (json)
    crash_report: str
