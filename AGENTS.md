# Repository Guidelines

## Project Structure & Module Organization

The repository contains one Python skill under `financial-calendar/`. Deterministic pipeline code lives in `financial-calendar/scripts/`: fetchers collect source data, `normalize.py` creates the unified event model, `diff_engine.py` detects changes, and `render.py` produces briefs. Configuration is under `financial-calendar/config/`; source notes are in `references/`; tests are in `tests/`. Generated JSON, snapshots, and Markdown reports belong in `data/` and `logs/` and are gitignored. Design and deployment documents live in `docs/`.

## Build, Test, and Development Commands

Create the local environment and install locked dependencies:

```bash
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.lock
```

Run validation before committing:

```bash
.venv/bin/python -m pytest -q financial-calendar/tests
.venv/bin/python -m compileall -q financial-calendar/scripts
python3 /home/hong/.codex/skills/.system/skill-creator/scripts/quick_validate.py financial-calendar
```

Use `.venv/bin/python financial-calendar/scripts/run.py --doctor` for source/config diagnostics. Generate briefs with `--tier=day|week|month`; add `--no-fetch` to render cached data.

## Coding Style & Naming Conventions

Use Python 3.13-compatible code, four-space indentation, type hints for public interfaces, and standard-library solutions where practical. Modules, functions, variables, and tests use `snake_case`; classes use `PascalCase`. Keep source failures explicit: an empty or unavailable response must never mean “no events.” Store timestamps in UTC and convert with `zoneinfo`; never hard-code UTC offsets.

## Testing Guidelines

Tests use pytest-compatible `unittest` classes. Name files `test_*.py` and methods `test_<behavior>`. Every correctness or failure-handling change requires a regression test. Mock external services in unit tests; use `run.py --doctor` for deliberate live-schema checks. Preserve the 15-line short-brief cap and idempotency coverage.

## Commit & Pull Request Guidelines

History follows Conventional Commit prefixes such as `feat:`, `fix:`, `test:`, `refactor:`, `docs:`, and `chore:`. Keep commits focused. Pull requests should explain behavior changes, list validation commands/results, identify affected sources/configuration, and document degradation behavior. Link relevant issues; include sample brief output when rendering changes.

## Security & Configuration

Never commit API keys, `.env`, runtime state, or generated reports. Secrets belong in environment variables or the gitignored root `.env`. Do not hand-write FRED release IDs or mark dates `verified: true` without `source` and `source_checked_at`.
