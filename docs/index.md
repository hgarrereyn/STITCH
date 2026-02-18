# STITCH Documentation

STITCH is a fuzzing system that combines a core fuzzing engine with LLM-driven workflow automation.

This site is intentionally minimal for now and serves as the starting point for repository documentation.

## Components

- `stitch_core`: Rust library for core runtime and mutation functionality
- `stitch_libafl`: LibAFL-based fuzzing engine
- `stitch`: top-level CLI package for project setup and orchestration
- `stitchi`: internal package used inside fuzzing containers

## Next Steps

- Expand command reference for the `stitch` CLI
- Add architecture documentation and data-flow diagrams
- Add contributor and development environment guides
