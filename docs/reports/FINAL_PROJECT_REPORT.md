# FSOCIETY AI 3.0.0 — Final Project Report

**Date:** 2026-11-23  
**Upstream baseline:** Heretic `aecd4a8` (2.2.0.dev0), AGPL-3.0-or-later  
**Delivery state:** local commit on `master` (see `git log`), **not pushed** (authorization required)  
**Validation host:** Linux x86_64, Python 3.14.10, Intel i5-8250U, 15.4 GiB RAM, **no GPU**, no Container runtime

---

## 1. Executive summary

FSOCIETY AI is delivered as a sidecar platform over the preserved Heretic engine: the
`heretic` package, CLI, configuration contract, algorithms, plugin interfaces and
reproducibility manifests remain intact, while a new `fsociety` layer adds hardware
detection with provenance, analytical resource planning, a torch-free model manager,
seeded Optuna optimization with persistence, a reproducible evaluation framework,
experiment storage, isolated plugins, a hardened HTTP API and a ten-section React
dashboard.

The engine was not reimplemented. The only behavioural change inside `src/heretic` is a
three-function distribution-name shim (`_distribution.py`) required because the
distribution was renamed to `fsociety-ai`; without it the engine crashes on startup. The
shim is covered by upstream tests and an installed-wheel check.

Verification results on this host:

| Profile | Result |
| :--- | :--- |
| Light platform, Python 3.14 (no torch) | **473 passed, 6 skipped** (upstream engine tests skip explicitly) |
| Light platform, Python 3.10 | **473 passed, 6 skipped** |
| Engine (Python 3.12, torch 2.14.1+cpu, transformers 5.18) | **500 passed, 3 skipped** |
| Upstream engine suite (unittest) | **47 passed** |
| Frontend | `tsc --noEmit` clean; production build clean; 10 routes exercised against the live API |
| Wheel | builds; contains `heretic/` + `fsociety/`; clean light-venv install + serve verified |
| Docker | **unverified** — no Docker/Podman on the host |

---

## 2. Environment constraints

* **No GPU and no CUDA/ROCm/MPS hardware.** GPU detection paths are unit-tested with
  simulated devices; GPU execution, CUDA memory behavior and GPU performance are **not**
  verified.
* **No container runtime.** Dockerfile/compose are written but not built here.
* **No model modification run.** A full Heretic modification requires a real model and
  hours of compute; upstream unit tests pass, but no end-to-end ablation was executed.
* **No external Hub downloads in tests.** Download code validates arguments and offline
  behavior; live snapshots were not fetched.
* **Model outputs in tests are test doubles.** CI evaluation uses `CallableBackend` under
  `tests/` only; no production path fabricates model output.
* The dashboard's 375 px layout could not be exercised through the browser tool (viewport
  resize unavailable); CSS breakpoints were reviewed, Lighthouse accessibility scored 1.0
  at the available viewport, and views are built mobile-first with a drawer navigation.

---

## 3. What was inspected

* `src/heretic` — all 21 modules (7,385 LOC): model loading, dtype fallback, meta-device
  footprint probe, residual/logit extraction, ARA/abliteration, scorers, plugin loader,
  configuration, batch-size benchmarking, export/upload and reproducibility.
* Configuration files (`config.*.toml`), migration guide, README, license.
* 47 upstream tests and the scripted model tests (`tests/run_tests.py`; not executed here
  because they download and mutate models).
* CI workflow, dependency set, packaging (`uv_build`, distribution `heretic-llm`).
* Git history/status: fork of upstream, no prior FSOCIETY implementation.

Findings are recorded in [`docs/audit/INITIAL_AUDIT.md`](../audit/INITIAL_AUDIT.md) and
[`docs/sdd/00_DISCOVERY.md`](../sdd/00_DISCOVERY.md).

---

## 4. What was added

| Area | Implementation | Evidence |
| :--- | :--- | :--- |
| Contracts | Pydantic models, typed errors, settings, workspace jail, hashing, redacted audit log | `tests/unit/test_foundation.py` |
| Hardware | CPU/RAM/NVIDIA/ROCm/torch/MPS detection with provenance, confidence, stable fingerprint | mocked CPU/NVIDIA/MPS tests |
| Planner | Analytical weights/KV/activation/overhead model, fit decisions, alternatives | estimator + planner tests |
| Model manager | Local/HF-cache inspection, exact safetensors parameter count, MoE/multimodal/remote-code detection, manifests/verification, guarded downloads | 65 model/engine tests |
| Engine adapter | Validated Heretic TOML, argv-only subprocess, trust policy | engine adapter tests; installed-wheel `heretic --help` |
| Optimizer | Declarative space, seeded TPE/random, persistence, failure isolation, cancellation, planner | 66 tests |
| Evaluation | 7 original suites (72 items), 8 deterministic graders, 3 backends, provenance reports, comparison rules, lm-eval adapter | 87 tests |
| Experiments | SQLite WAL store for experiments/trials/jobs/events/artifacts, JSON export | store + concurrency tests |
| Plugins | Versioned contract, discovery, permissions/deps validation, subprocess isolation, 7 example scorers | 55+ plugin/security tests |
| Security | Trust policy, path jail, redaction, API auth/rate limit/headers/TrustedHost, findings, SBOM, dependency audit | security tests |
| API | `/api/v1` system/models/jobs/experiments/evaluation/plugins/exports/security/settings, structured errors, cancellable jobs | 14 API + 8 integration tests |
| CLI | 13 command groups covering operations, engine adapter and security | 7 CLI integration tests |
| Dashboard | React 19 + TS strict + Vite, 10 sections, design tokens, real data, loading/empty/error, job polling | typecheck/build/browser |
| Delivery | Docker, compose, installer, CI, SDD docs, guides, migration, security policy, changelog | see `deployment/`, `docs/` |

**Removed:** nothing functional. The only replaced artifact is the packaging metadata
(`heretic-llm` → `fsociety-ai`, `uv_build` → `hatchling`), with compatibility shims
documented in [`MIGRATION.md`](../guides/MIGRATION.md) and ADR-007/011.

---

## 5. Tests executed

### 5.1 Platform, light profile (no torch)

```bash
PYTHONPATH=src /tmp/opencode/fsociety-venv/bin/python -m pytest tests -q -m "not gpu and not engine"
# 473 passed, 6 skipped, 3 subtests passed (Python 3.14 and 3.10)
```

Skips: the five upstream engine test modules require torch and are reported explicitly
(`pytest.importorskip("torch")`), not passed.

### 5.2 Platform, engine profile (Python 3.12)

```bash
/tmp/opencode/engine-env/bin/python -m pytest tests -q -m "not gpu"
# 500 passed, 3 skipped, 1 warning, 5 subtests passed
```

Skips: the deliberate no-engine failure paths (evaluation backend/API) cannot be
exercised when torch is installed.

### 5.3 Preserved engine

```bash
/tmp/opencode/engine-env/bin/python -m unittest discover -s tests -p 'test_*.py'
# Ran 47 tests ... OK
```

### 5.4 Static analysis

* `ruff check src/fsociety tests src/heretic/_distribution.py` — clean.
* `ruff format --check` — clean.
* `ty check src/fsociety --ignore import-not-found` — clean (optional torch/transformers
  imports intentionally lazy).
* `uv build` — wheel + sdist built; wheel contains 81 `fsociety` files, 21 `heretic`
  files and both console scripts.

### 5.5 Frontend

```bash
npm run typecheck   # tsc --noEmit, strict: exit 0
npm run build       # 403.67 kB JS (119.24 kB gzip), 43.68 kB CSS (10.66 kB gzip)
```

Browser E2E against the live API: Overview, Models, Hardware, Optimizer, Evaluation,
Experiments, Plugins, Exports, Security, Settings all render real data; console clean;
Lighthouse accessibility 1.0, best practices 1.0, SEO 0.8; no horizontal overflow at the
tested viewport. An optimization job submitted from the UI succeeded and linked its
experiment (`ui-smoke` → `exp_20261123T105935Z_505dd42e`).

### 5.6 Installation

* Clean Python 3.12 venv + light requirements + wheel (`--no-deps`) → `fsociety version`,
  `hardware --json`, `evaluate suites --json` work.
* Installed wheel serves `/api/v1/health` and the built dashboard (`/` HTML, `/api/docs`
  200) with `FSOCIETY_FRONTEND_DIST`.

### 5.7 Not executed

* GPU tests — no device.
* Docker image build — no runtime.
* Full Heretic modification run — requires a model and hours; upstream tests pass.
* Live Hugging Face download — network-dependent, argument/offline paths unit-tested.
* CI on GitHub Actions — workflow written, not run in this environment.
* Official MMLU/GSM8K/HumanEval — intentionally not bundled; optional harness adapter.

---

## 6. Security findings

Full report: [`.factory/SECURITY_AUDIT.md`](../../.factory/SECURITY_AUDIT.md).
**Final status: APPROVED, 0 open CRITICAL, 0 open HIGH, 1 accepted MEDIUM (sqlitedict), 3 accepted LOW (public API docs, non-lockfile Docker build, Python 3.10 filelock)** for the Python 3.11+/3.12
production artifacts (the audited engine profile and the light/API profile). Python 3.10
is supported for the light/API profile only.

Findings opened and closed during the adversarial pass:

| ID | Severity | Finding | Resolution |
| :--- | :--- | :--- | :--- |
| VULN-001 | HIGH | Plugin discovery imported workspace/entry-point code in the host | Closed: discovery now runs inside the isolation worker; hostile-module test proves no host execution |
| VULN-002 | MEDIUM | API absolute model references escaped the workspace jail | Closed: filesystem references confined to workspace/models (`400 unsafe_path`) |
| VULN-004 | HIGH | Engine profile pinned vulnerable Pillow/sqlitedict | Closed for 3.11+: Pillow 13, certifi/filelock/nltk/dnspython/pyasn1 floors; sqlitedict residual accepted (no fix, cache never ingested) |
| VULN-005 | MEDIUM | Transitive dependency advisories (TLS store, TOCTOU, DoS) | Closed for 3.11+ |
| VULN-006 | MEDIUM | `dependency_audit()` trusted a PATH binary | Closed: `sys.executable -m pip_audit`, env allowlist, deduplicated output |
| VULN-003 | MEDIUM | CSRF on loopback bodiless endpoints | Closed: Origin validation rejects foreign/`null` origins (403) |
| VULN-007 | LOW | Chunked body bypassed the size cap | Closed: chunked-without-length → 413; compressed bodies → 415 |
| VULN-008 | LOW | Missing HSTS / `Cache-Control` | Closed: `no-store` on `/api/*`; HSTS when served over HTTPS |
| VULN-012 | MEDIUM | Python 3.10 engine resolved vulnerable deps | Closed by packaging: engine deps marker-gated to 3.11+; 3.10 install is light/API-only |
| VULN-014 | LOW | Python 3.10 light profile resolves filelock with local-only advisories | Accepted: fixed release requires 3.11; local-only, no remote/API path |

**Accepted residual risks (documented, not release-blocking):**
1. Plugin isolation is process-level, not a container/VM boundary; `load()`/in-process is
   explicit opt-in for trusted code.
2. Rate limiting and job execution are per-process; multi-worker deployments need shared state.
3. `sqlitedict` transitive advisory in `lm-eval` (no fixed release): the engine never passes
   `use_cache`, the API cannot invoke arbitrary harness tasks, and no external cache is
   ingested. Track upstream.
4. OpenAPI/docs schema is public (no data); disable with `FSOCIETY_API_DOCS_ENABLED=false`
   on internet-facing hosts.
5. Docker/GPU execution was not exercised on this host (availability, not security approval).
6. API keys live in `sessionStorage`, never persisted; CSP is strict for scripts and allows
   inline styles/data fonts for the bundled UI.
7. Upstream Heretic's optional IPython shell remains engine-CLI-only, disabled by default and
   never exposed through the API.

---

## 7. Performance measurements

Only measurements actually taken are reported:

| Measurement | Value | Conditions |
| :--- | :--- | :--- |
| Platform test suite | ~40 s (light) / ~158 s (engine) | local CPU, includes subprocess plugin-discovery tests |
| Frontend bundle | 406.94 kB JS / 45.03 kB CSS | Vite production build |
| Gzip bundle | 119.78 kB JS / 10.91 kB CSS | Vite report |
| SBOM generation | 6.7 kB, 50 components | Python 3.12 engine env |
| Dependency audit (engine env) | 1 unique advisory (`sqlitedict`, no fix; `use_cache` never used) after upgrading Pillow/certifi/filelock/nltk/dnspython/pyasn1; light profile: 0 | `python -m pip_audit` |
| Optimization (4 trials, tiny model) | ~1 s job | analytical objective, CPU |
| Upstream engine unit suite | 0.53 s | 47 tests |

**No model throughput, VRAM, latency or modification-time benchmark was run**; any such
number would require GPU hardware and a real model. The platform refuses to present
estimates as measurements.

---

## 8. Compatibility findings

* `import heretic`, `python -m heretic.main`, `heretic --help` and the `heretic` console
  script work under the new distribution name (verified on the installed wheel; the first
  attempt failed and was fixed).
* Heretic 2.x TOML keys, scorer/modifier plugin contracts, export formats and
  reproducibility manifests are unchanged.
* Platform plugins are a separate contract (`fsociety`, API version 1) and are not a
  replacement for engine plugins.
* Distribution rename requires uninstalling `heretic-llm` to avoid two packages owning the
  `heretic` script. This and rollback are documented.
* Five upstream test modules gained `pytest.importorskip` guards so the light profile
  reports them as skipped instead of failing collection. No assertions were changed.
* Old reproduction manifests pinned to `heretic-llm` 2.x will report a version mismatch,
  as they would for any engine version change; reproduction still demands an identical
  environment.

---

## 9. Known limitations and incomplete items

1. **GPU/CUDA/ROCm/MPS behavior unverified** (no hardware). Detection logic is tested with
   simulated devices.
2. **Docker image unbuilt** (no runtime). Files reviewed but not executed.
3. **Bundled suites are smoke tests**, not leaderboard benchmarks; no confidence intervals.
4. **Plugin isolation is not a container**; cross-kernel/user escape is out of scope.
5. **No multi-tenant auth/RBAC**; single operator, API key.
6. **Live Hub download and real model evaluation** not exercised end-to-end.
7. **Responsive 375/768** was exercised via headless Chromium (CDP device metrics) by the UI review: no page overflow on any route; the in-app browser itself cannot resize the viewport.
8. **Frontend dist is not packaged into the wheel**; the dashboard is served from
   `frontend/dist`, `FSOCIETY_FRONTEND_DIST`, or the container image. API works without it.
9. **GGUF parameter counting** is not implemented; conversion/inference requires llama.cpp
   tools and is planned/executed honestly only when present.
10. **CI workflow not executed on GitHub**; local equivalents were executed.
11. **No push/release** was made; delivery is a local commit only, per authorization policy.
12. **Engine profile requires Python 3.11+** because security-fixed dependency releases do; Python 3.10 is light/API-only (enforced by markers).

---

## 10. Acceptance criteria status

| AC | Status | Evidence |
| :--- | :--- | :--- |
| AC-01 | Verified | 47 upstream tests pass (engine profile) |
| AC-02 | Verified | wheel + `heretic --help`; import check |
| AC-03 | Verified | LICENSE, SPDX headers, attribution preserved |
| AC-04 | Verified | `fsociety hardware --json` (trailing flag supported) |
| AC-05 | Verified | recorded warnings, no raise |
| AC-06 | Verified | simulated CPU/NVIDIA/MPS tests |
| AC-07 | Verified | assumptions returned with every estimate |
| AC-08 | Verified | alternatives + recommendations test |
| AC-09 | Verified | numeric/quant/batch tests |
| AC-10 | Verified | scan/inspect without torch |
| AC-11 | Verified | manifest tamper test; CLI non-zero |
| AC-12 | Partially verified | argument/offline logic tested; live download not run |
| AC-13 | Verified | trials persisted; API/CLI E2E |
| AC-14 | Verified | planner constraint tests |
| AC-15 | Verified | seeded determinism test |
| AC-16 | Verified | provenance fields in report + integration test |
| AC-17 | Verified | comparability rules tests |
| AC-18 | Verified | no production fake backend; no-engine failure path |
| AC-19 | Verified | 8-thread store test; WAL |
| AC-20 | Verified | config/suite hashes + hardware fingerprint tests |
| AC-21 | Verified | invalid API version/base/permission tests |
| AC-22 | Verified | crash/timeout/output-flood isolation tests; job cancellation test |
| AC-23 | Verified | example scorer tests |
| AC-24 | Verified | traversal/symlink/absolute tests |
| AC-25 | Verified | trust policy tests |
| AC-26 | Verified | non-loopback auth tests (fail closed) |
| AC-27 | Verified | redaction in logs/errors/reports tests |
| AC-28 | Verified | API validation/error envelope tests |
| AC-29 | Verified | job manager cancellation; API runs without torch |
| AC-30 | Verified | OpenAPI schema test |
| AC-31 | Verified | typecheck + production build |
| AC-32 | Verified (error path) | 401 error state rendered with retry; transport-offline shares the component |
| AC-33 | Verified | ten routes in browser |
| AC-34 | Verified | clean light venv + installed CLI |
| AC-35 | **Unverified** | no container runtime on host |
| AC-36 | Verified | this report |

---

## 11. Recommended next roadmap

1. Run the engine test suite and a small end-to-end modification on a CUDA host; record
   real VRAM/latency measurements.
2. Build the Docker image in CI and publish a signed image with an SBOM attestation.
3. Add a shared rate limiter and an external job queue for multi-worker deployments.
4. Containerize plugin execution (gVisor/Firecracker/OCI) for a real security boundary.
5. Expand evaluation suites with licensed official benchmark adapters and statistical
   reporting (confidence intervals, multiple seeds).
6. Add authentication roles if multi-user operation becomes a requirement.
7. Vendor the dashboard build into the wheel or publish it as a separate artifact.
8. Implement GGUF metadata parsing and a verified llama.cpp export pipeline.
9. Add Playwright visual regression at 375/768/1440 in CI.
10. Commit and publish under the AGPL with signed releases after maintainer review.

---

## 12. Reproduce this validation

```bash
# Light
uv venv /tmp/fsociety-light --python 3.14
uv pip install --python /tmp/fsociety-light/bin/python -r deployment/docker/requirements-platform.txt pytest ruff ty
uv pip install --python /tmp/fsociety-light/bin/python --no-deps -e .
PYTHONPATH=src /tmp/fsociety-light/bin/python -m pytest tests -q -m "not gpu and not engine"

# Engine (CPU)
uv venv /tmp/fsociety-engine --python 3.12
UV_PROJECT_ENVIRONMENT=/tmp/fsociety-engine uv sync --extra api --extra dev
/tmp/fsociety-engine/bin/python -m pytest tests -q -m "not gpu"
/tmp/fsociety-engine/bin/python -m unittest discover -s tests -p 'test_*.py'
/tmp/fsociety-engine/bin/heretic --help

# Frontend
cd frontend && npm install && npm run typecheck && npm run build
```

**No claims are made for tests not executed.**
