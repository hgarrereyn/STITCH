# Quickstart

Requires Python 3.10+, Git, Docker with `linux/amd64` support, and an OpenAI API key. Start Docker first.

```bash
pip install stitch-fuzz
export OPENAI_API_KEY="your-api-key"

stitch init https://github.com/madler/zlib
cd madler_zlib

stitch gen                  # Configure the build
stitch infer --name harn1    # Infer a harness
stitch fuzz --name harn1     # Fuzz and triage crashes
```

To install from an artifact checkout, replace `pip install stitch-fuzz` with `pip install ./stitch`, run at the repository root.

The first run builds Docker images. Setup and inference time depend on the target; LLM steps use your API account. Press Ctrl+C to request a graceful stop, then rerun `stitch fuzz --name harn1` to resume.

## Project layout

Each target has a project directory. Build settings are shared; each harness keeps its own fuzzing and coverage results.

```text
madler_zlib/
├── stitch.json             Source revision and project settings
├── config/                 Dockerfile, build metadata, and test program
├── harnesses/
│   └── harn1/              One directory per harness
│       ├── harness.json    Inferred harness specification
│       ├── campaign/
│       │   ├── reports/    Crash reports and triage results
│       │   └── revisions/  Harness revisions and fuzzing outputs
│       └── coverage/       HTML and JSON coverage reports
└── bugs/                   Exported C++ reproducers
```

Main files and folders are shown above; they appear as you run each stage. Raw fuzzing writes to the directory given by `-o` (`outputs/` in the coverage example below).

## Reproduce bugs

From a project with triaged campaign reports:

```bash
stitch bugs replay --name harn1
```

This recompiles and runs minimized testcases with AddressSanitizer. Add `-b <bug-hash>` to select a report by its directory name under `harnesses/harn1/campaign/reports/`.

To run a saved C++ reproducer in the configured project:

```bash
stitch bugs run -t testcase.cpp
```

Add `-d` for the debugger or `-c <commit>` to test another target revision.

## Measure coverage

From a project with an inferred harness, run a one-hour experiment:

```bash
stitch fuzz --name harn1 --no-triage -o outputs \
  --time-limit-seconds 3600
stitch cover --name harn1 --outputs outputs --nproc 4
```

The time limit excludes the build. The raw fuzzing run does not use LLM triage, and coverage requires a nonempty `outputs/queue/`.

Open `harnesses/harn1/coverage/html/index.html` for the report, or read `harnesses/harn1/coverage/coverage.json`. Coverage profiles accumulate within a harness; use a fresh project copy for each independent trial. Pin the target with `stitch init <repo-url> -c <commit>` and retain its generated configuration and harness for repeatable experiments.

## Inspect a project

```bash
stitch info
stitch info --harness harn1
```

These show the source revision, harnesses, recorded LLM usage, and harness statistics. See the [command reference](cli-reference.md) for more options.
