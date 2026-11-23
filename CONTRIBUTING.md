# Contributing to FSOCIETY AI

## Ground rules

1. **The engine is frozen for platform work.** Changes under `src/heretic` must be
   motivated by an engine bug or feature and separately tested; platform features belong
   in `src/fsociety` adapters.
2. **Spec-driven.** Substantial changes update `docs/sdd/` (requirements → architecture →
   plan) and `.factory/TASKS.md` before code.
3. **No placeholders.** `TODO`, `FIXME`, mock data in production paths and truncated
   implementations are rejected.
4. **No fabricated evidence.** Do not claim a test passed, a benchmark improved or a
   platform was verified if it was not executed. Report skips explicitly.
5. **Licensing.** Contributions are AGPL-3.0-or-later. Keep the SPDX header and upstream
   attribution. Do not copy incompatible benchmark data or code.

## Development setup

```bash
uv venv
uv sync --extra api --extra dev   # engine + API + dev tools
uv run pytest -q
uv run ruff check src/fsociety tests
uv run ruff format --check src/fsociety tests
cd frontend && npm install && npm run typecheck && npm run build
```

Light profile (no torch, useful for API work):

```bash
uv venv /tmp/fsociety-light
uv pip install --python /tmp/fsociety-light/bin/python \
  pydantic pydantic-settings rich tomli-w psutil py-cpuinfo numpy optuna \
  huggingface-hub langdetect pytest httpx fastapi uvicorn ruff
PYTHONPATH=src /tmp/fsociety-light/bin/python -m pytest -q
```

## Layout and ownership

| Path | Responsibility |
| :--- | :--- |
| `src/heretic/` | preserved engine |
| `src/fsociety/core/` | contracts, hardware, estimator, model manager, engine adapter |
| `src/fsociety/optimizer/` | search spaces, Optuna engine, planner, objectives |
| `src/fsociety/evaluation/` | suites, graders, backends, runner, reports, regression |
| `src/fsociety/plugins/` | plugin API, registry, isolation, examples |
| `src/fsociety/security/` | policy, findings, redaction, SBOM, headers |
| `src/fsociety/experiments/` | SQLite store |
| `src/fsociety/interfaces/` | CLI and API |
| `frontend/` | React dashboard |
| `tests/` | unit, integration, api, security, fixtures |

Do not duplicate the memory math outside `core/estimator.py`; do not import torch at
module scope; every new external input gets a Pydantic schema or a `safe_join` check.

## Testing policy

* New behavior requires a test that fails without the change.
* Hardware/engine-specific tests use `@pytest.mark.gpu` / `@pytest.mark.engine` and
  `pytest.importorskip`; a skipped test is reported as skipped.
* Never assert on wall-clock performance on shared CI runners. Performance claims need a
  recorded benchmark and a correctness test.
* Security-sensitive changes add a test under `tests/security/`.
* Frontend changes require `npm run typecheck` and `npm run build`.

## Commit and review

* Small, atomic commits with a clear scope (`feat(models): …`, `fix(api): …`).
* Include the verification command and result in the PR description.
* Do not commit secrets, tokens, model weights or generated `dist/` output.
* Do not push releases or publish packages without authorization.
