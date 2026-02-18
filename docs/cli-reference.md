# CLI Reference

All commands are invoked via `poetry run stitch <command>` (or just `stitch` inside a Poetry shell).

## Core Commands

### `stitch init <repo-url>`

Clone and initialize a target project.

| Option | Description |
|--------|-------------|
| `-c <commit>` | Pin to a specific commit hash |

### `stitch gen`

Run the Configure Agent to generate build artifacts in `config/`.

**Outputs:** `Dockerfile`, `test.cpp`, `info.json`, `usage.json`

### `stitch infer`

Launch the Inference Workflow to produce a harness specification.

| Option | Description |
|--------|-------------|
| `--threads <n>` | Parallelize compilation tasks |
| `--name <name>` | Label for the harness (default: `harn1`) |

**Output:** `harnesses/<name>/harness.json`

### `stitch fuzz`

Start a fuzzing campaign in loop mode. Automatically triages crashes and patches the schema between revisions. Restartable.

**Output:** `harnesses/<name>/campaign/` (includes `reports/`)

### `stitch fuzz-raw`

Run the fuzzer continuously without LLM-assisted triage.

| Option | Description |
|--------|-------------|
| `-o <dir>` | Output directory for artifacts |

## Utility Commands

### `stitch info`

Display project metadata and accumulated LLM costs.

### `stitch dev`

Launch an interactive shell inside the fuzzer Docker container.

### `stitch test`

Run the configuration `test.cpp` compilation pass.

### `stitch stats`

| Option | Description |
|--------|-------------|
| `--harness <name>` | Run constructability analysis on a specific harness |

### `stitch replay`

| Option | Description |
|--------|-------------|
| `-b <hash>` | Replay a minimized crash from a report folder |

### `stitch bug`

| Option | Description |
|--------|-------------|
| `-t <file.cpp>` | Replay an external reproducer |
| `-d` | Launch with `pwndbg` for debugging |

### `stitch install-completions`

Auto-detect the current shell and install tab completions (with confirmation prompt).

### `stitch completion show`

| Option | Description |
|--------|-------------|
| `--shell <shell>` | Print the completion script for the given shell (e.g. `zsh`) |
