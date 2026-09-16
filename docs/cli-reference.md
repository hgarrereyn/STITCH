# Command reference

Use `stitch --help` to list commands and `stitch <command> --help` for all options. Project commands default to the current directory. Use `--name <name>` to select a harness when more than one exists.

## Setup and fuzzing

| Command | Purpose | Useful options |
| --- | --- | --- |
| `stitch init <source> [path]` | Initialize from a Git URL or local source directory | `-c <commit>`, `-m <model>` |
| `stitch gen` | Generate the Docker build configuration | `-e <context>`, `-m <model>` |
| `stitch test` | Verify the build and code generation configuration | — |
| `stitch infer` | Infer a harness specification | `--name <name>`, `--threads <n>`, `-m <model>` |
| `stitch fuzz` | Fuzz with automatic triage and harness repair | `--name <name>`, `--max-revisions <n>` |
| `stitch fuzz --no-triage -o <dir>` | Run the raw fuzzer and save its corpus | `--time-limit-seconds <n>` |

`stitch gen`, `stitch infer`, and full-mode `stitch fuzz` require `OPENAI_API_KEY`. Raw fuzzing, replay, and coverage can use an existing configuration and harness without LLM calls.

## Bugs and coverage

| Command | Purpose | Useful options |
| --- | --- | --- |
| `stitch bugs list <projects-dir>` | List reports across project directories | `--severity <level>` |
| `stitch bugs replay` | Recompile and replay minimized campaign testcases | `-b <bug-hash>`, `-c <commit>` |
| `stitch bugs save` | Save a minimized reproducer to `bugs/<hash>.cpp` | `-b <bug-hash>` |
| `stitch bugs run -t <testcase.cpp>` | Compile and run a standalone reproducer | `-d` (debugger), `-c <commit>` |
| `stitch bugs exec -t <testcase.json>` | Execute a raw graph testcase | `--name <name>` |
| `stitch bugs triage` | Triage saved crash buckets | `--watch`, `--nproc <n>` |
| `stitch bugs analyze --crashes <dir>` | Analyze raw crash files | `-o <dir>` |
| `stitch cover` | Measure source coverage from the latest campaign corpus | `--name <name>`, `--nproc <n>` |
| `stitch cover --outputs <dir>` | Measure source coverage from raw fuzzing output | `--name <name>`, `--nproc <n>` |

Coverage reports are written to `harnesses/<name>/coverage/`. See the [quickstart examples](getting-started.md) for a complete timed run.

## Utilities

| Command | Purpose |
| --- | --- |
| `stitch info` | Show project details, harnesses, and recorded LLM usage |
| `stitch info --harness <name>` | Show detailed harness statistics |
| `stitch dev` | Open a shell in the target Docker container |
| `stitch install-completions` | Install shell completions interactively |

Older command names such as `fuzz-raw`, `replay`, `bug`, and `stats` have been replaced by `fuzz --no-triage`, `bugs replay`, `bugs run`, and `info --harness`, respectively.
