# Getting Started

These are minimal setup instructions for using STITCH.

## Install the CLI

```bash
cd stitch
poetry install
poetry run stitch --help
```

## Enable Shell Completion (optional)

```bash
poetry run stitch install-completions
```

## Typical Workflow

1. Initialize a target project:

   ```bash
   poetry run stitch init <repo-url>
   ```

2. Generate configuration:

   ```bash
   poetry run stitch gen
   ```

3. Infer a harness specification:

   ```bash
   poetry run stitch infer
   ```

4. Start fuzzing:

   ```bash
   poetry run stitch fuzz
   ```

For deeper details, see the repository `README.md`.
