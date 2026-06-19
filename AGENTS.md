# Repository Guidelines

## Project Posture
The README is the target product contract, not a complete inventory of the current implementation. This product has not launched; refactors should implement the README and current requirements directly.

Do not add compatibility logic for old implementations, old commands, old fields, old data formats, or old behavior. Ask first when intent or scope is unclear.

## Project Structure
This is a Python 3.12 Feishu bot project. Core code lives in `app/`, and tests live in `tests/`.

Main modules:

- `app/config.py`: environment variables and runtime configuration.
- `app/ofox_client.py`, `app/feishu_client.py`: external API clients.
- `app/repository.py`, `app/models.py`: SQLite persistence and domain models.
- `app/commands.py`, `app/reports.py`, `app/handlers.py`, `app/worker.py`: commands, reports, event handling, and the worker.

## Environment & Dependencies
Use the Python 3.12 version pinned by `.python-version`. Use only `uv` project commands for dependency management; do not use `uv pip install`.

Dependency changes must be reflected in both `pyproject.toml` and `uv.lock`. Runtime configuration is loaded from `.env`; do not commit `.env` or real credentials. Keep example configuration in `.env.example`.

## Build, Test, and Verification
After changing Python code, run at least:

```bash
uv run --locked ruff check
uv run --locked ruff format --check
uv run --locked python -m compileall app
uv run --locked pytest
```

For documentation or configuration-only changes, decide whether full verification is needed based on risk. If verification is skipped, state that in the handoff.

## Coding Style
Follow the existing module boundaries and naming style. Prefer the project's existing helpers, models, and client wrappers.

Use Ruff for formatting and linting. Add short comments only near non-obvious control flow or business constraints; do not explain individual lines that are already clear.

## Data & Artifacts
The local SQLite database defaults to `data/ofox.sqlite3` and is a runtime artifact. Do not commit it. Do not commit logs, caches, `.venv/`, `__pycache__/`, or other local runtime artifacts.
