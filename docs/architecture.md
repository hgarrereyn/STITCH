# Architecture

STITCH is composed of a Rust fuzzing core and Python orchestration layer, connected through Docker containers.

## High-Level Overview

```
┌─────────────────────────────────────────────────┐
│                  stitch (CLI)                    │
│         Project setup · Orchestration            │
│              Configure Agent (LLM)               │
├─────────────────────────────────────────────────┤
│              Docker Container                    │
│  ┌───────────────────────────────────────────┐  │
│  │            stitchi (internal)              │  │
│  │   Inference Workflow · Triage Agent        │  │
│  │            Patch Agent                     │  │
│  ├───────────────────────────────────────────┤  │
│  │           stitch_libafl                   │  │
│  │        LibAFL fuzzing engine              │  │
│  ├───────────────────────────────────────────┤  │
│  │           stitch_core                     │  │
│  │   Runtime · Mutation · Execution          │  │
│  └───────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
```

## Package Responsibilities

### `stitch_core` (Rust)

The foundational library handling:

- Testcase representation and serialization
- Mutation strategies
- Runtime execution of test programs

### `stitch_libafl` (Rust)

Built on top of [LibAFL](https://github.com/AFLplusplus/LibAFL), this package implements:

- Coverage-guided fuzzing loop
- Crash detection and minimization
- Integration with `stitch_core` for testcase handling

### `stitch` (Python)

The user-facing CLI and orchestration layer:

- `stitch init` — clone and set up a target project
- `stitch gen` — run the Configure Agent to produce build artifacts
- `stitch infer` — launch the Inference Workflow
- `stitch fuzz` / `stitch fuzz-raw` — start fuzzing campaigns

### `stitchi` (Python)

Installed inside fuzzer Docker containers, this package runs:

- **Inference Workflow** — generates harness specifications from target code
- **Triage Agent** — analyzes crashes and produces diagnostic reports
- **Patch Agent** — modifies the harness schema to work around crash-causing issues

## Data Flow

1. The user runs `stitch init` + `stitch gen` to set up a project and generate a build configuration.
2. `stitch infer` launches a container with `stitchi` to produce a harness specification (`harness.json`).
3. `stitch fuzz` starts the fuzzing campaign. On each crash:
    - The **Triage Agent** diagnoses the root cause.
    - The **Patch Agent** optionally repairs the schema.
    - Fuzzing resumes from the next revision.
