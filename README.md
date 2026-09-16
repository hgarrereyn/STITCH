<p align="center">
  <a href="https://hgarrereyn.github.io/STITCH/">
    <img src="docs/assets/stitch_banner.png" alt="STITCH banner" />
  </a>
</p>

<p align="center">
  <a href="https://pypi.org/project/stitch-fuzz/"><img alt="PyPI" src="https://img.shields.io/pypi/v/stitch-fuzz?color=1d4ed8"></a>
  <a href="https://pypi.org/project/stitch-fuzz/"><img alt="Python Version" src="https://img.shields.io/pypi/pyversions/stitch-fuzz?color=0f766e"></a>
  <a href="https://hgarrereyn.github.io/STITCH/"><img alt="Documentation" src="https://img.shields.io/badge/docs-read-blue"></a>
</p>

STITCH is an LLM-assisted fuzzing framework that performs fully-automatic, flexible fuzzing of C and C++ libraries.

## Quickstart

Requires Python 3.10+, Git, Docker with `linux/amd64` support, and an OpenAI API key. Start Docker first.

```bash
pip install stitch-fuzz
export OPENAI_API_KEY="your-api-key"
stitch init https://github.com/madler/zlib
cd madler_zlib
stitch gen # generate build configuration
stitch infer --name harn1 # infer a harness specification
stitch fuzz --name harn1 # start a fuzzing campaign
```

To install from an artifact checkout, use `pip install ./stitch` at the repository root. The first run builds Docker images; LLM steps use your API account.

Press Ctrl+C to request a graceful stop. Rerun `stitch fuzz --name harn1` to resume. Reports are saved in `harnesses/harn1/campaign/reports/`.

## Overview

Fuzzing is performed with the high-level `stitch` CLI which manages sandboxed fuzzing containers for each target.  

## Common Commands

```bash
stitch bugs replay --name harn1         # reproduce triaged bugs
stitch fuzz --name harn1 --no-triage -o outputs --time-limit-seconds 3600
stitch cover --name harn1 --outputs outputs --nproc 4
stitch info                            # project and LLM usage details
```

## Learn More

- [Documentation site](https://hgarrereyn.github.io/STITCH/): quickstart and examples
- [Getting Started](docs/getting-started.md): setup, bug reproduction, and coverage
- [CLI Reference](docs/cli-reference.md): command details and flags
- [Architecture](docs/architecture.md): system design and data flow
- [Docs Home](docs/README.md): full documentation index
