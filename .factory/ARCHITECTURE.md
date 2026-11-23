# FSOCIETY AI — SYSTEM ARCHITECTURE & DESIGN SYSTEM

- **Status:** `LOCKED` (revised only through documented decisions)
- **Runtime:** Python 3.10+ · Pydantic v2 · FastAPI (optional) · React 19 + TypeScript + Vite · SQLite (WAL)
- **Engine:** upstream Heretic 2.2.0.dev0, preserved unchanged (`src/heretic`)
- **Last Updated By:** `@architect` / Principal Orchestrator

---

## 1. TECH STACK & PINNED DEPENDENCIES

| Layer | Technology | Constraint | Purpose |
| :--- | :--- | :--- | :--- |
| Engine | heretic (in-repo) | 2.2.0.dev0 | Model modification, ARA/abliteration, scorers, grimoire |
| Platform | fsociety | 3.0.0 | Hardware, models, planner, evaluation, plugins, API, CLI |
| Validation | pydantic v2 + pydantic-settings | `~=2.15` | Strict trust-boundary schemas |
| Optimization | Optuna TPE | `~=5.0` | Real seeded hyperparameter search |
| Storage | SQLite (stdlib) | WAL | Experiments, trials, jobs, events, artifacts |
| API | FastAPI + uvicorn | optional extra `[api]` | `/api/v1`, structured errors |
| Frontend | React 19 + TS + Vite | Node 22 build | Ten-section operator dashboard |
| Hashing | sha256 (stdlib) | — | Artifacts, manifests, fingerprints |
| Isolation | subprocess + rlimits + optional `unshare -n` | POSIX | Untrusted plugin execution |

**Dependency boundaries:** importing `fsociety` or `fsociety.interfaces.api` must not import torch. Engine-dependent code lazy-imports `torch`/`transformers`/`heretic` inside functions. FastAPI is optional for CLI-only installs.

---

## 2. DIRECTORY & MODULE TOPOLOGY

```text
src/
├── heretic/                    # UPSTREAM ENGINE — DO NOT MODIFY
│   ├── main.py  model.py  config.py  plugin.py  scorer.py
│   ├── modifiers/{ara,abliteration}.py
│   ├── scorers/*.py
│   └── utils.py  system.py  reproduce.py  evaluator.py
└── fsociety/
    ├── __init__.py             # version, PLUGIN_API_VERSION
    ├── config.py               # PlatformSettings (TOML + env), trust policy
    ├── context.py              # composition root (store, logger, hardware cache)
    ├── errors.py               # typed error hierarchy with HTTP status
    ├── logging.py              # redacted JSONL audit log
    ├── paths.py                # safe_join + Workspace
    ├── core/
    │   ├── models.py           # all shared Pydantic contracts
    │   ├── hardware.py         # provenance-aware detection
    │   ├── estimator.py        # analytical memory model
    │   ├── engine.py           # Heretic adapter (no shell, config writer)
    │   └── model_manager/      # safetensors/HF cache inspection, integrity
    ├── optimizer/              # space, Optuna engine, planner, resource objective
    ├── evaluation/             # suites, graders, backends, runner, reports, regression
    ├── plugins/                # versioned API, registry, subprocess isolation, examples
    ├── security/               # redaction, policy, findings, SBOM, headers
    ├── exporters/              # verifiable bundles, GGUF boundary
    ├── experiments/store.py    # SQLite persistence
    ├── utils/hashing.py        # sha256 file/dir/manifest/verify
    └── interfaces/
        ├── cli/main.py         # `fsociety` command
        ├── api/                # FastAPI app, auth, jobs, routes
        └── (dashboard is built in frontend/ and served by the API)
```

**Forbidden:** new top-level packages, duplicate memory math outside `core/estimator.py`, importing torch at module scope, editing `src/heretic` to add platform features (use adapters).

---

## 3. DATA FLOW

```mermaid
flowchart LR
  CLI[fsociety CLI] --> CTX[PlatformContext]
  API[FastAPI /api/v1] --> CTX
  UI[React dashboard] --> API
  CTX --> STORE[(SQLite WAL)]
  CTX --> HW[Hardware detector]
  CTX --> LOG[Audit JSONL]
  API --> JOBS[JobManager threads]
  JOBS --> OPT[Optuna optimizer]
  JOBS --> EVAL[Evaluation runner]
  OPT --> EST[Resource estimator]
  EVAL --> BACKEND[Transformers / llama.cpp]
  EVAL --> STORE
  EVAL --> REPORTS[reports/*.json|md]
  ENGINE[Heretic engine] -.lazy import.-> CTX
  PLUGINS[Isolated plugins] --> EVAL
```

**Job lifecycle:** `queued → running → succeeded|failed|cancelled`. Cancellation is cooperative; tokens mirror into SQLite. Interrupted jobs are marked failed on restart.

**Reproducibility envelope (every experiment):** seed, config hash, suite hash, hardware fingerprint, software versions, timestamps, artifacts with sha256. Reports are only comparable under matching suite/config/grader conditions; hardware mismatch is surfaced as a warning, not silently ignored.

---

## 4. SECURITY BOUNDARIES

| Boundary | Control |
| :--- | :--- |
| HTTP → API | API key (constant-time), loopback trust, TrustedHost, body-size cap, sliding-window rate limit, CORS allowlist (no `*`) |
| API → filesystem | `safe_join` jail, absolute-path policy, export root restriction, report path containment |
| Model repository → process | `trust_remote_code` never by default; explicit allowlist of `org/name`; interactive upstream path documented |
| Plugin → host | subprocess, env allowlist, timeout, output cap, `start_new_session`, optional `unshare -n`, rlimits; documented as imperfect isolation |
| Logs/reports | `redact()` on every audit line, API error and stored report |
| Secrets | never committed; API settings expose only `api_key_configured` |
| Supply chain | CycloneDX SBOM, optional pip-audit, dependency inventory |

---

## 5. UI DESIGN SYSTEM (DASHBOARD)

**Palette:** graphite `#0B0F12` base, navy `#0D1520` surface, raised `#121C28`, border `#1E2A36`, text `#E6EDF3`, muted `#8B9AA8`, neon green `#3DFFA2` accent (primary actions/active only), danger `#FF5C5C`, warning `#FFC857`, info `#5AB0FF`.
**Typography:** Inter Variable (UI), JetBrains Mono (data, IDs, hashes). Scale 12/13/14/16/20/28/40, line-height 1.4–1.6.
**Spacing:** 4/8/12/16/24/32/48. Radius 6/10/14. Borders 1px. Shadows only for overlays (0 12px 32px rgba(0,0,0,.45)).
**Motion:** 120–180ms ease-out; honor `prefers-reduced-motion`.
**Components:** AppShell (sidebar + mobile drawer), PageHeader, Card, StatTile, DataTable (horizontal scroll + card collapse), Skeleton, EmptyState, ErrorState with code + retry, JobProgress (aria-live), Badge, Drawer, KeyValueGrid, MemoryBar, Sparkline (pure SVG), Tabs.
**States:** loading skeleton → data | empty with CTA | error with request code and Retry. No fabricated values. API offline is an explicit state.
**Accessibility:** skip link, landmarks, labelled controls, visible focus, `aria-current`, AA contrast, no color-only status.
**Responsive:** 375 (single column, bottom/drawer nav), 768 (two columns, collapsible sidebar), 1440 (sidebar + 12-col grid).

---

## 6. ACCEPTANCE GATE RULES

1. `pytest` (unit + security + API integration) green; skips explicitly reported.
2. `ruff check` and `ruff format --check` green for `src/fsociety` and new tests.
3. `frontend`: `npm run typecheck` + `npm run build` green.
4. Every AC in `.factory/SPEC.md` mapped to a test or a documented, honest skip.
5. `.factory/SECURITY_AUDIT.md` has 0 open CRITICAL/HIGH.
6. No TODO/placeholder in production paths; no secrets in diff/logs.
