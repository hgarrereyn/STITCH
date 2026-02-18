# STITCH

**LLM-driven fuzzing that combines a core fuzzing engine with automated workflow orchestration.**

---

STITCH automates the process of generating fuzz harnesses, running fuzzing campaigns, and triaging crashes — all powered by large language models.

## Components

| Package | Language | Description |
|---------|----------|-------------|
| `stitch_core` | Rust | Core runtime, testcase execution, and mutation |
| `stitch_libafl` | Rust | LibAFL-based fuzzing engine (uses `stitch_core`) |
| `stitch` | Python | Top-level CLI for project setup, orchestration, and the Configure Agent |
| `stitchi` | Python | Internal package for fuzzer containers — Inference Workflow, Triage Agent, and Patch Agent |

## LLM Agents

STITCH uses four LLM-powered components to drive the fuzzing workflow:

- **Config Agent** — generates project build configuration and a test program
- **Inference Workflow** (codename *Zephyr*) — infers a harness specification from the target project
- **Triage Agent** — diagnoses crashes found during fuzzing
- **Patch Agent** — repairs the schema and restarts the fuzzer after a crash

## Quick Start

```bash
cd stitch
poetry install
poetry run stitch init <repo-url>
poetry run stitch gen
poetry run stitch infer
poetry run stitch fuzz
```

!!! tip "New to STITCH?"
    Head over to the [Getting Started](getting-started.md) guide for a full walkthrough.
