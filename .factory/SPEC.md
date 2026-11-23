# FSOCIETY AI — PROJECT SPECIFICATION (SINGLE SOURCE OF TRUTH)

- **Project Name:** FSOCIETY AI — Advanced Model Engineering Platform
- **Status:** `VERIFIED_EXCEPT_ENVIRONMENTAL_GAPS`
- **Target Stack:** Python 3.10+ (Heretic engine preserved) · Pydantic v2 · FastAPI · React 19 + TypeScript + Vite · SQLite
- **Last Updated By:** `@architect`
- **Upstream basis:** Heretic 2.2.0.dev0 (AGPL-3.0-or-later, Philipp Emanuel Weidmann + contributors)

---

## 1. SCOPE & BOUNDARIES

### 1.1 In-Scope
- Preserve the complete Heretic modification engine (`heretic` package, CLI, plugins, configs) unchanged and usable.
- FSOCIETY platform package (`fsociety`) layered on top: hardware detection, resource estimation, model manager, optimizer, evaluation/benchmarking, experiment tracking, plugin ecosystem, security controls, FastAPI API, React dashboard, CLI.
- SDD documentation, audit, security report, tests, CI, Docker, migration guide.
- Backward compatibility: `heretic` import path and `heretic` console script remain available.

### 1.2 Out-of-Scope
- Training or fine-tuning models.
- Running GPU workloads/benchmarks on this machine (no torch/GPU available) — must be reported as unverified/skipped, never fabricated.
- Publishing releases or pushing to remote (`murdok1982/HereticFsociety`) without explicit authorization.
- Arbitrary Python execution through the API.
- Claiming benchmark parity across differing evaluation conditions.

---

## 2. USER ROLES & ACCESS MATRIX

| Role | Description | Auth Required | Allowed Resources / Actions |
| :--- | :--- | :--- | :--- |
| `operator` | Local single-user platform | Local-only binding | All read/write endpoints from loopback |
| `api-client` | Authenticated remote client | API key (`X-API-Key` / Bearer) | Read endpoints + submitted jobs |
| `anonymous` | Unauthenticated remote | No | `/api/v1/health`, `/api/v1/version` only |
| `plugin` | Untrusted third-party code | — | Restricted: runs isolated, no settings access |

---

## 3. FUNCTIONAL REQUIREMENTS & ACCEPTANCE CRITERIA

### F1 — Historical engine preservation
- **REQ-01:** The upstream `heretic` package, console script, configs and tests remain intact.
  - [x] **AC-01:** `python -m unittest discover -s tests -p 'test_*.py'` (upstream tests) passes where dependencies exist; skipped otherwise.
  - [x] **AC-02:** `heretic` remains a declared console script and `import heretic` still resolves.
  - [x] **AC-03:** Upstream license headers and AGPL notices are preserved; distribution metadata keeps the original attribution.

### F2 — Hardware detection
- **REQ-02:** Detect CPU, RAM, NVIDIA/AMD/Apple accelerators from real sources, never assuming OS implies capability.
  - [x] **AC-04:** `fsociety hardware --json` emits a `HardwareProfile` with per-field `source` provenance and `confidence`.
  - [x] **AC-05:** Missing `nvidia-smi`/torch does not raise; backend reported available=false with a warning.
  - [x] **AC-06:** Unit tests cover CPU-only profile, mocked NVIDIA profile, mocked MPS profile.

### F3 — Resource estimation
- **REQ-03:** Estimate weights, KV cache, activations, overhead and batch/precision recommendations from model metadata.
  - [x] **AC-07:** Estimates are pure functions with documented assumptions; `kind="estimate"` always attached.
  - [x] **AC-08:** Insufficient-resource requests return actionable alternatives (quantization/dtype/batch/device map).
  - [x] **AC-09:** Numerical unit tests cover 4-bit/8-bit/16-bit/32-bit and batch scaling.

### F4 — Model management
- **REQ-04:** Discover local and Hugging Face cached models, read metadata/revisions, validate compatibility, hash artifacts.
  - [x] **AC-10:** Scanning a directory of model folders returns `ModelInfo` without importing torch.
  - [x] **AC-11:** `fsociety models verify` compares SHA-256 manifest and reports mismatches non-zero exit.
  - [ ] **AC-12:** HF downloads require explicit revision pinning or return the resolved commit. *(Argument/offline/trust logic unit-tested; live Hub download not executed on this host.)*

### F5 — Optimizer
- **REQ-05:** Run real hyperparameter optimization (Optuna TPE, seeded) over a declarative search space with hardware constraints.
  - [x] **AC-13:** Optimizer minimizes a supplied objective and persists every trial to SQLite.
  - [x] **AC-14:** Constraint validation rejects configs exceeding detected memory with explanation.
  - [x] **AC-15:** Identical seed+space+objective yields identical trial suggestions.

### F6 — Evaluation
- **REQ-06:** Reproducible evaluation with bundled suites, deterministic graders, provenance metadata, baseline comparison, JSON+Markdown reports.
  - [x] **AC-16:** Running a suite with a real backend records model id, suite hash, seed, hardware fingerprint, config, timings.
  - [x] **AC-17:** Comparisons refuse to mark conditions as comparable when suite hash, grader version or model differ materially.
  - [x] **AC-18:** No fabricated model outputs in production paths; CI uses a scripted backend explicitly labelled as a test double.

### F7 — Experiments
- **REQ-07:** Persist experiments, trials, jobs, events, artifacts in SQLite with JSON export.
  - [x] **AC-19:** Concurrent writes from API job threads do not corrupt the store (WAL + lock).
  - [x] **AC-20:** Every experiment has hardware fingerprint and config hash.

### F8 — Plugins
- **REQ-08:** Versioned plugin API, discovery, validation, audit log, subprocess isolation with timeouts.
  - [x] **AC-21:** Invalid API version or wrong base class is rejected with a clear error.
  - [x] **AC-22:** Plugin crash/timeout does not crash the host process; error is recorded.
  - [x] **AC-23:** Example plugins are importable and unit-tested.

### F9 — Security
- **REQ-09:** Path jail, secret redaction, explicit `trust_remote_code` policy (default deny), API auth/rate-limit/security headers, SBOM, dependency audit.
  - [x] **AC-24:** Path traversal attempts (`../../etc/passwd`, symlink escape) are rejected.
  - [x] **AC-25:** `trust_remote_code` is denied unless the exact repo id is allowlisted.
  - [x] **AC-26:** Remote API access without a configured key is refused (loopback exempt).
  - [x] **AC-27:** Logs/reports never contain raw tokens matching known secret patterns.

### F10 — API
- **REQ-10:** FastAPI v1 API: health, hardware, models, optimization jobs, evaluation jobs, experiments, plugins, exports, security, settings.
  - [x] **AC-28:** All endpoints validate input with Pydantic and return structured errors.
  - [x] **AC-29:** Jobs are cancellable and report progress; API starts without torch installed.
  - [x] **AC-30:** OpenAPI schema generated successfully and tested.

### F11 — Dashboard
- **REQ-11:** React/TS dashboard using only real API data with loading/empty/error states, responsive 375/768/1440.
  - [x] **AC-31:** Production build succeeds (`npm run build`) and typecheck passes.
  - [x] **AC-32:** API-offline state renders an explicit error, not mock metrics.
  - [x] **AC-33:** All ten sections exist and are reachable.

### F12 — Delivery
- **REQ-12:** Docs, Docker, CI, migration guide, final report.
  - [x] **AC-34:** Fresh install + `fsociety doctor` works in a clean venv (light profile).
  - [ ] **AC-35:** Docker CPU image builds and `/api/v1/health` returns ok. *(Dockerfile/compose written and reviewed; no container runtime on this host. Not verified.)*
  - [x] **AC-36:** `docs/reports/FINAL_PROJECT_REPORT.md` accurately lists passed/failed/skipped tests and limitations.

---

## 4. NON-FUNCTIONAL REQUIREMENTS
- **NFR-01:** Core platform imports must not require torch, transformers, fastapi or Node.
- **NFR-02:** Reproducibility metadata (seed, config hash, suite hash, hardware fingerprint) on every experiment.
- **NFR-03:** No secrets in repository, logs, API responses or reports.
- **NFR-04:** Errors surfaced with actionable messages; no silent exception swallowing.
- **NFR-05:** Backward compatibility for heretic configuration keys and plugin interfaces.
- **NFR-06:** No placeholder/mock in production paths (test doubles isolated under tests).
- **NFR-07:** Every performance claim evidence-based; unavailable hardware reported as skipped.
