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

Read our preprint: [Automatic, Expressive, and Scalable Fuzzing with Stitching](todo).

## Quick Start (60 seconds)

```bash
pip install stitch-fuzz
export OPENAI_API_KEY=...
stitch init https://github.com/madler/zlib
cd madler_zlib
stitch gen # generate build configuration
stitch infer # infer a harness specification
stitch fuzz # start a fuzzing campaign
```

That flow initializes a target repo, generates configuration, infers a harness, and starts a restartable fuzzing loop with automated triage.

## Overview

Fuzzing is performed with the high-level `stitch` CLI which manages sandboxed fuzzing containers for each target.  

## Common Commands

```bash
stitch fuzz                 # loop mode (triage + patch workflow)
stitch fuzz-raw -o outputs  # raw continuous fuzzing
stitch info                 # project and token usage details
stitch install-completions  # shell completion setup
```

## Learn More

- [Getting Started](docs/getting-started.md): prerequisites and full walkthrough
- [CLI Reference](docs/cli-reference.md): command details and flags
- [Architecture](docs/architecture.md): system design and data flow
- [Docs Home](docs/README.md): full documentation index
