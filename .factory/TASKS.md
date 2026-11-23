# FSOCIETY AI — AUTONOMOUS EXECUTION BACKLOG

- **Current Phase:** `PHASE_2` (intelligence & ecosystem) → `PHASE_3` (production readiness)
- **Progress:** 19 / 20 verified (95%) — 13 phase tasks + 6 security fixes; TASK-012 is partial only because no container runtime is available (image unverified).
- **Legend:** `- [ ]` pending · `- [~]` in progress · `- [x]` verified · `- [!]` blocked (3/3)

## PHASE 0 — STABILIZATION & FOUNDATION

- [x] **TASK-001:** Audit repository, record baseline, initialize `.factory/` state and SDD docs.
  - **Owner:** `@architect` · **Spec:** §1, AC-01..03 · **Verify:** git status, docs present
  - **Retries:** `0/3`
- [x] **TASK-002:** Shared contracts (`core/models.py`), typed errors, settings (TOML+env), workspace path jail, redaction/hashing, audit log, SQLite store, hardware detection, analytical estimator.
  - **Owner:** `@builder` → `@qa-verifier` · **Spec:** AC-04..09, AC-19..20, AC-24, AC-27
  - **Verify:** `pytest tests/unit/test_foundation.py` (32 passed)
  - **Retries:** `0/3`

## PHASE 1 — CORE EVOLUTION

- [x] **TASK-003:** Model manager: safetensors header parsing, HF cache discovery, integrity manifests, Heretic engine adapter.
  - **Owner:** `@builder` · **Spec:** AC-10..12 · **Verify:** `pytest tests/unit/test_model_manager.py tests/unit/test_engine_adapter.py`
  - **Retries:** `0/3`
- [x] **TASK-004:** Optimizer: declarative search space, seeded Optuna engine, persistence, cancellation, planner.
  - **Owner:** `@builder` · **Spec:** AC-13..15 · **Verify:** `pytest tests/unit/test_optimizer.py tests/unit/test_planner.py`
  - **Retries:** `0/3`

## PHASE 2 — INTELLIGENCE & ECOSYSTEM

- [x] **TASK-005:** Evaluation framework: bundled suites, deterministic graders, real backends, provenance reports, regression comparison, lm-eval adapter.
  - **Owner:** `@builder` · **Spec:** AC-16..18 · **Verify:** `pytest tests/unit/test_evaluation.py tests/unit/test_graders.py tests/unit/test_regression.py`
  - **Retries:** `0/3`
- [x] **TASK-006:** Plugins (versioning, discovery, isolation), example scorers, export bundles, GGUF boundary, trust policy, SBOM, security headers.
  - **Owner:** `@builder` · **Spec:** AC-21..27 · **Verify:** `pytest tests/unit/test_plugins.py tests/unit/test_exporters.py tests/security`
  - **Retries:** `0/3`
- [x] **TASK-007:** FastAPI v1 API with auth, rate limit, headers, durable cancellable jobs, structured errors, SPA serving.
  - **Owner:** Principal Orchestrator · **Spec:** AC-28..30 · **Verify:** `pytest tests/api`
  - **Retries:** `0/3`
- [x] **TASK-008:** CLI (`fsociety`) covering doctor, hardware, models, optimize, evaluate, experiments, plugins, security, export, serve, config, engine.
  - **Owner:** Principal Orchestrator · **Spec:** AC-34 · **Verify:** CLI help + doctor + JSON outputs
  - **Retries:** `0/3`
- [x] **TASK-009:** React dashboard: 10 sections, design system, real API data, loading/empty/error, responsive, API key handling.
  - **Owner:** `@builder` + `@ui-artisan` review · **Spec:** AC-31..33 · **Verify:** `npm run typecheck && npm run build`
  - **Evidence (2026-11-23):** 12 page components / 10 nav routes; `frontend/dist` built (2.7 MB); SPA served by the API (HTTP 200, `index.html` 696 B). Browser E2E of all 10 routes against the live API, Lighthouse a11y 1.0 and zero console errors are reported in `docs/reports/FINAL_PROJECT_REPORT.md` §5.5 (not re-run in this documentation pass: no Node on this host).
  - **Retries:** `0/3`

## PHASE 3 — PRODUCTION READINESS

- [x] **TASK-010:** Documentation: README, install/quickstart, architecture, CLI/API/config reference, plugin/optimizer/benchmark/security guides, troubleshooting, migration, changelog.
  - **Owner:** Principal Orchestrator · **Spec:** AC-36 · **Verify:** docs review against CLI `--help` and OpenAPI
  - **Evidence (2026-11-23):** README rewritten; 11 guides under `docs/guides/`; CHANGELOG/SECURITY/CONTRIBUTING/.env.example present; CLI `--help`, OpenAPI and endpoint paths cross-checked; `docs/reports/FINAL_PROJECT_REPORT.md` final. Audit approved (0 HIGH/CRITICAL).
  - **Retries:** `0/3`
- [x] **TASK-011:** API/CLI integration tests, backward-compat tests, OpenAPI schema test, security tests.
  - **Owner:** `@qa-verifier` · **Spec:** AC-28..30, AC-24..27 · **Verify:** `pytest tests`
  - **Evidence (2026-11-23):** final sweep — light 473 passed/6 skipped; engine 500 passed/3 skipped; upstream 47 OK; API/security/integration suites green; OpenAPI/auth sweep pass.
  - **Retries:** `0/3`
- [~] **TASK-012:** CI matrix (3.10–3.14, light profile + engine profile), Docker CPU image, install script.
  - **Owner:** `@builder` · **Spec:** AC-35 · **Verify:** `docker build` (if Docker available) / CI config review
  - **Evidence (2026-11-23):** CI written with `platform` (3.10/3.12/3.14 light), `quality`, `engine` (3.12), `frontend` (typecheck/build/npm audit) and `upstream-model-tests` jobs; `deployment/docker/{Dockerfile,docker-compose.yml}` and `deployment/scripts/install.sh` present. Local equivalents of every non-Docker job pass. **Docker image build is unverified** (no daemon) — AC-35 stays skipped. CI itself has not been observed running on GitHub in this environment.
  - **Retries:** `0/3`
- [x] **TASK-013:** Full verification sweep: ruff, pytest, frontend build, LICENSE/attribution, secret scan, SBOM.
  - **Owner:** `@qa-verifier` + `@redteam-auditor` · **Spec:** Exit gate · **Verify:** see `.factory/SECURITY_AUDIT.md`
  - **Evidence (2026-11-23 final):** `ruff check`/`ruff format --check` green (144 files), `ty check src/fsociety` green, frontend typecheck/build green, secret scan 0 production findings, SBOM generated, `.factory/SECURITY_AUDIT.md` = APPROVED (0 CRITICAL/0 HIGH). Remaining environmental gaps: Docker image and GPU execution unverified; GitHub Actions not observed.
  - **Retries:** `0/3`
- [x] **TASK-014:** Final report with passed/failed/skipped tests, security findings, performance evidence, compatibility, limitations, roadmap.
  - **Owner:** Principal Orchestrator · **Spec:** AC-36 · **Verify:** `docs/reports/FINAL_PROJECT_REPORT.md` (471+ tests, security APPROVED, limitations recorded)
  - **Evidence (2026-11-23):** report final with light 473/6, engine 500/3, Python 3.10 light 472/6 (before the last two HTTP tests), upstream 47, dependency-audit results, security approval, Docker/GPU limitations and AC matrix.
  - **Retries:** `0/3`

## PHASE 3 — SECURITY REMEDIATION QUEUE (ADR-013)

- [x] **SEC-FIX-001 (VULN-001, HIGH):** Run plugin discovery for workspace plugins and entry points inside the isolation worker; never import untrusted modules in the host during `PluginRegistry(...)`.
  - **Owner:** `@builder` · **Spec:** AC-21..23, `.factory/SECURITY_AUDIT.md` VULN-001 · **Verify:** `pytest tests/security/test_plugin_isolation_adversarial.py tests/unit/test_plugins.py`
  - **Evidence (2026-11-23):** `discover_plugins_isolated` + `worker.py:discover_plugins`; `test_discovery_does_not_execute_plugin_code_in_the_host` (host marker absent, worker marker present); `test_explicit_host_load_executes_plugin_code` documents the opt-in `load()` residual. 202/201 tests pass in light/engine profiles.
  - **Retries:** `0/3`
- [x] **SEC-FIX-002 (VULN-004/005, HIGH/MEDIUM):** Raise engine dependency security floors (Pillow, certifi, filelock, nltk, dnspython, pyasn1), re-lock and re-audit; assess sqlitedict.
  - **Owner:** `@builder` · **Spec:** AC-24..27, VULN-004/005 · **Verify:** `uv lock --check`, `pip-audit` in the engine env
  - **Evidence (2026-11-23):** `uv.lock` synced (`pillow 13.0.0`, `certifi 2026.10.23`, `filelock 4.2.10`, `nltk 3.10.4`, `dnspython 2.9.2`, `pyasn1 0.6.5`); `pip-audit` reports 1 unique advisory (sqlitedict, VULN-013). Python 3.10 engine gap closed by SEC-FIX-004/VULN-012 (LOW filelock residual VULN-014).
  - **Retries:** `0/3`
- [x] **SEC-FIX-003 (VULN-006, MEDIUM):** Audit the active interpreter (`sys.executable -m pip_audit`) with an environment allowlist; normalize `name` and deduplicate advisories.
  - **Owner:** `@builder` · **Spec:** VULN-006 · **Verify:** `pytest tests/security/test_sbom.py`
  - **Evidence (2026-11-23):** `security/sbom.py:144-192`; engine run `finding_count=1` (sqlitedict), light run `unavailable`; 4 sbom tests pass.
  - **Retries:** `0/3`
- [x] **SEC-FIX-004 (VULN-012, MEDIUM scope):** Resolve the Python 3.10 engine dependency gap by marker-gating every engine dependency to `python_version >= '3.11'`; keep 3.10 as light/API-only.
  - **Owner:** `@architect` + `@builder` · **Spec:** `.factory/SECURITY_AUDIT.md` VULN-012 · **Verify:** `uv export --frozen --no-dev --python 3.10`; clean 3.10 install audit
  - **Evidence (2026-11-23):** engine deps carry `python_full_version >= '3.11'`; `uv export --python 3.10` shows no torch/transformers/datasets/lm-eval/Pillow; clean CPython 3.10.22 install + `pip_audit` shows only `filelock 4.1.0` (17 local advisories) → accepted LOW VULN-014. `uv lock --check` exit 0.
  - **Retries:** `0/3`
- [x] **SEC-FIX-005 (VULN-003, MEDIUM):** Reject foreign/`null` Origin on loopback state-changing requests; allow same-origin and configured CORS origins.
  - **Owner:** `@builder` · **Spec:** VULN-003 · **Verify:** `pytest tests/security/test_api_security.py`
  - **Evidence (2026-11-23):** `OriginCheckMiddleware`; `test_foreign_origin_state_change_is_rejected` (403 evil/null, 200 same-origin, no-Origin CLI unaffected). 158 security tests pass in both profiles.
  - **Retries:** `0/3`
- [x] **SEC-FIX-006 (VULN-007/008/010, LOW):** HTTP hardening batch: chunked-body and compressed-body rejection, `Cache-Control: no-store`, scheme-aware HSTS, CI `npm audit`.
  - **Owner:** `@builder` · **Spec:** VULN-007/008/010 · **Verify:** `pytest tests/security -q`; `npm audit --audit-level=high`
  - **Evidence (2026-11-23):** `BodySizeLimitMiddleware` (413 TE-without-CL, 415 compressed), `RequestContextMiddleware` (no-store + HTTPS HSTS), CI frontend `npm audit` step; `npm audit` 0 vulnerabilities (125 deps). Residual notes: OriginCheck 403 headers and ASGI TE+CL server dependency (§6.11/6.12).
  - **Retries:** `0/3`
