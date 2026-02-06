# End-to-end Automatic Modular Fuzzing




## Build folder components

```
build/
├─ <project>/
│  ├─ base.Dockerfile     # Base image for the project
│  ├─ Dockerfile          # (autogen) Dockerfile for the project
│  ├─ info.json           # (autogen) Project build configuration
│  ├─ test.cpp            # (autogen) Test client file
│  ├─ <harness>/
│  │  ├─ harness.json     # (autogen) Harness configuration
│  │  ├─ queue/           # (fuzz) Queue of test cases
│  │  ├─ crashes/         # (fuzz) Crashes found by the harness
│  │  ├─ runtime.json     # (fuzz) Runtime information
│  ├─ triage/
```


## Usage

Initialize a project from a github repository.
```
stitch init <github_url> [path]
```

To print only the full path of the initialized project directory (useful for scripting):
```
stitch init -q <github_url> [path]
```

Run auto-generation.
```
stitch gen
```

Run inference.
```
stitch infer [--harness <harness_name>]
```

Run fuzzing.
```
stitch fuzz [--harness <harness_name>]
```

Run triage (tries to reproduce crashes).
```
stitch triage [--harness <harness_name>]
```

For debugging, you can mount the project in a container and run the fuzzer manually.
```
stitch dev [--harness <harness_name>]
```

### Internal commands

Internal commands are prefixed with `stitch internal` and not intended to be invoked directly by the user.

## stitch.yaml:

```
repo=<github_url>
commit=<commit_hash> (optional, uses HEAD if not specified)

repro_per_crash=<int> (optional, default 20) -- defines how many external reproducers to try to generate for each crash
llm_min_per_crash=<int> (optional, default 1) -- defines how many LLM-based minimization steps to try for each crash
```

## Project structure



```
<project_name>/
    - stitch.yaml # Project description
    - config/
        - Dockerfile # (gen) Dockerfile for the project
        - info.json # (gen) Project build configuration
        - test.cpp # (gen) Test client file
    - harness/
        - <harness_name>/
            - harness.json # (infer)
            - output/
                crashes/
                queue/
                runtime_metadata.json
            - analysis/
                - reports/
                    - <crash_name>.report # (triage) json report or failure
                - buckets/
                    - <hash>/
                        - desc.txt # (triage) crash description
                        - repro/
                            - <crash_name>.cpp # (triage) graph-minimized external code
                            - <crash_name>.report # (triage) crash report for the external code
                        - minimized/
                            - <crash_name>.cpp # (triage) llm-minimized external code
                            - <crash_name>.report # (triage) crash report for the llm-minimized code
```


