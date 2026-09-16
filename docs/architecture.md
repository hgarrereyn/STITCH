# Architecture

STITCH has a Python orchestration layer and a Rust fuzzing engine. Target builds and fuzzing run in Docker containers.

| Package | Responsibility |
| --- | --- |
| `stitch` | User-facing CLI, project setup, build configuration agent, and container management |
| `stitchi` | Container-side harness inference, crash triage, and harness repair |
| `stitch_core` | Testcase representation, execution, and mutation |
| `stitch_libafl` | Coverage-guided fuzzing built on LibAFL |

## Workflow

1. `stitch init` records the target source and revision.
2. `stitch gen` generates a Dockerfile, build metadata, and a test program in `config/`.
3. `stitch infer` writes a harness specification to `harnesses/<name>/harness.json`.
4. `stitch fuzz` runs the fuzzer, triages crashes, and repairs the harness between revisions. Results persist in `harnesses/<name>/campaign/`.

Use `stitch fuzz --no-triage -o <dir>` to run an existing harness without LLM triage or repair. `stitch cover --outputs <dir>` replays its saved corpus against a coverage-instrumented build.

See the [quickstart](getting-started.md) for runnable examples.
