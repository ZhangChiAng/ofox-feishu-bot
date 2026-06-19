# Repository Guidelines

## Project Posture
The README is the target product contract, not a complete inventory of the current implementation. This product has not launched; refactors should implement the README and current requirements directly.

Do not add compatibility logic for old implementations, old commands, old fields, old data formats, or old behavior. Ask first when intent or scope is unclear.

## Project Structure
This is a Python 3.12 Feishu bot project. Core code lives in `app/`.

## Environment & Dependencies
Use the Python 3.12 version pinned by `.python-version`. Use only `uv` project commands for dependency management; do not use `uv pip install`.

Dependency changes must be reflected in both `pyproject.toml` and `uv.lock`. Runtime configuration is loaded from `.env`; do not commit `.env` or real credentials. Keep example configuration in `.env.example`.

## Build, Test, and Verification
After changing Python code, run at least:

```bash
uv run --locked ruff check app
uv run --locked ruff format --check app
uv run --locked python -m compileall app
uv run --locked pytest
```

## Coding Style
Follow Google Python Style for organization, naming, imports, docstrings, and readability. Use Ruff as the formatting and linting tool.

Add short inline comments inside functions to explain non-obvious implementation logic. Explain intent and control flow, not what an individual line already says.
