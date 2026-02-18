# STITCH

This repository contains the implementation of STITCH.

The fuzzer consists of two components:
- `stitch_core`: a Rust library that handles core functionality, runtime testcase execution, testcase mutation, etc.
- `stitch_libafl`: the core fuzzing engine built with LibAFL, this uses `stitch_core` as a dependency.

There are two packages that manage the high level workflow and operate the LLM integration:
- `stitch` is the main top-level python package that acts as a CLI for STITCH, it also contains the Configure Agent
- `stitchi` ("STITCH Internal") is the python package that gets installed inside fuzzer docker containers and manages the internal fuzzing loop, containing the Inference Workflow, Triage Agent, and Patch Agent.

Note that this repository is still in a bit of a prototype state, everything is runnable but there is some extra unused functionality remaining from our tests which we plan to clean up before the full public release.

## LLM reference

- `Config Agent` -> [stitch/stitch/gen_agent/agent.py](stitch/stitch/gen_agent/agent.py)
- `Inference Workflow` -> (codename "Zephyr") -> [stitchi/stitchi/zephyr/run.py](stitchi/stitchi/zephyr/run.py)
- `Triage Agent` -> [stitchi/stitchi/zephyr/distill_agent.py](stitchi/stitchi/zephyr/distill_agent.py)
- `Patch Agent` -> [stitchi/stitchi/zephyr/patcher_agent.py](stitchi/stitchi/zephyr/patcher_agent.py)

## Usage

Install the `stitch` CLI with Poetry:
```
cd stitch
poetry install
poetry run stitch --help
```

If you want `stitch` available directly in your shell:
```
poetry shell
```

Enable shell completion (supports harness-aware `--name` completion):
```
poetry run stitch install-completions
```
or to print a completion script manually:
```
poetry run stitch completion show --shell zsh
```

Normal usage is to configure a project, then infer a specification, and then run the fuzz loop:

For example, to test [zlib](https://github.com/madler/zlib), do the following:

Set your OpenAI API key (fuzzing zlib will cost about $1 using GPT-5):
```
export OPENAI_API_KEY=...
```

Set up a new project (optionally pin to a specific commit with `-c <commit>`):
```
poetry run stitch init https://github.com/madler/zlib
```

This will create a new project folder, here with the name `madler_zlib`. Next run the configure agent to generate a configuration:
```
cd madler_zlib
poetry run stitch gen
```

Once generation completes, you should see a `config` folder with the following files:
- `Dockerfile`: the Dockerfile for the project
- `test.cpp`: a simple test program using the project API
- `info.json`: the project build configuration
- `usage.json`: the usage tokens for the LLM calls

Next, run the inference workflow to infer a specification:
```
poetry run stitch infer
```
You can run with `--threads <n>` to use multiple threads for the compilation tasks, which will be faster. You can also give this harness a specific name with `--name <name>` (by default it is `harn1`).

Once this completes, you should have a `harnesses` folder with a new harness `harn1/harness.json` containing all the generated code blocks (as well as all the other metadata from the inference workflow).

Next, you can chose to fuzz in two modes.

Raw fuzzing mode (i.e. just run the fuzzer continuously) -- which is what we did for RQ1:
```
poetry run stitch fuzz-raw -o outputs
```
This will run the fuzzer continuously until you stop it, saving all the artifacts to the specified output directory.

Alternatively, can run STITCH in loop mode (normal mode) -- which is what we did for RQ2. This will run until hitting a crash and then use the Triage agent to diagnose the crash and (optionally) the Patch agent to repair the schema and restart the fuzzer.

```
poetry run stitch fuzz
```

This command is restartable, so if it generates two revisions and then you stop it, it will properly resume the campaign from the third revision. All data gets saved to the `campaign` directory which is in the harness folder (i.e. `harnesses/harn1/campaign`). Of interest, this will contain a `reports` folder containing the triaged crashes.

## Other tools

There are a few other tools used mainly for debugging:
- `stitch info` -> display a bunch of information about the project and costs
- `stitch dev` -> launch a shell inside the fuzzer docker container
- `stitch test` -> run the configuration test.cpp pass
- `stitch stats --harness <name>` -> run a constructability analysis on a specific harness
- `stitch replay -b <hash>` -> replay a specific minimized crash contained in a report folder
- `stitch bug -t <file.cpp>` -> replay an external reproducer for a bug, optionally add `-d` to launch `pwndbg`
- `stitch install-completions` -> auto-detect shell and install autocompletion (with y/n prompt)
