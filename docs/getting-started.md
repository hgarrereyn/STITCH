# Getting Started

## Prerequisites

- Python 3.11+
- [Poetry](https://python-poetry.org/) for dependency management
- Docker (for running fuzzer containers)
- An OpenAI API key

## Install the CLI

```bash
cd stitch
poetry install
```

Verify the installation:

```bash
poetry run stitch --help
```

!!! tip "Shell access"
    Run `poetry shell` to make `stitch` available directly without the `poetry run` prefix.

## Enable Shell Completion

=== "Auto-install"

    ```bash
    poetry run stitch install-completions
    ```

=== "Manual (zsh)"

    ```bash
    poetry run stitch completion show --shell zsh
    ```

## Typical Workflow

### 1. Initialize a target project

Point STITCH at a Git repository. Optionally pin a specific commit with `-c <commit>`.

```bash
poetry run stitch init https://github.com/madler/zlib
cd madler_zlib
```

### 2. Generate configuration

Run the Configure Agent to produce a `Dockerfile`, `test.cpp`, `info.json`, and `usage.json` inside a `config/` folder.

```bash
poetry run stitch gen
```

### 3. Infer a harness specification

Run the Inference Workflow to generate harness code blocks.

```bash
poetry run stitch infer
```

!!! info "Performance tip"
    Pass `--threads <n>` to parallelize compilation tasks. Use `--name <name>` to label the harness (defaults to `harn1`).

### 4. Start fuzzing

STITCH supports two fuzzing modes:

=== "Loop mode (default)"

    Runs until a crash is found, then uses the Triage and Patch agents to diagnose and repair.
    Restartable — resumes from the last revision automatically.

    ```bash
    poetry run stitch fuzz
    ```

=== "Raw mode"

    Runs the fuzzer continuously without LLM-assisted triage. Useful for benchmarking.

    ```bash
    poetry run stitch fuzz-raw -o outputs
    ```

Results are saved to the `harnesses/<name>/campaign/` directory, including a `reports/` folder with triaged crashes.
