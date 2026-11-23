# 01 — Requirements

- **Derived from:** `.factory/SPEC.md` (authoritative, unchanged at 36 ACs) + `docs/audit/INITIAL_AUDIT.md` + `docs/sdd/00_DISCOVERY.md`.
- **Initial snapshot:** commit `aecd4a8`, 2026-11-23T10:37Z (pre-remediation audit).
- **This revision:** 2026-11-23T11:20Z, post-remediation reconciliation. Test counts below are the **latest measured** values (473 LIGHT / 500 ENGINE); they grew during this pass as other agents landed tests, so earlier figures (337/364, and 341/368) are historical, not wrong. The security audit is now **APPROVED** (0 CRITICAL/0 HIGH). Working tree dirty; `src/heretic/_distribution.py` and ADR-011 exist; `dist/` cleaned after build verification.
- **Rule:** every requirement below is testable. Status never claims a pass that was not measured or explicitly attributed to another verifier. Requirement IDs and structure are unchanged from the initial revision; nothing was deleted.

## Status legend

| Status | Meaning |
| :--- | :--- |
| **Implemented** | Code exists and an automated check or executed command passed on 2026-11-23 (evidence cited inline and in §10). |
| **In progress** | Code exists but part of the acceptance evidence is missing, environment-blocked, or only inspected by reading. |
| **Not started** | No implementation. |
| **Skipped (environment)** | Cannot be verified on this host for a stated prerequisite reason; never counted as a pass. |
| **Excluded** | Explicitly out of scope (see §6). |

Environments: **LIGHT** = `/tmp/opencode/fsociety-venv` (no torch/transformers/optuna/lm-eval); **ENGINE** = `/tmp/opencode/engine-env` (torch, transformers, optuna, lm-eval 0.4.13); **UI** = Node 22 + `npm ci` (unavailable on this host); **DOCKER** = Docker daemon (unavailable on this host).

---

# 1. Functional requirements

## 1.1 Engine preservation and integration

### FR-001 — Preserve the upstream Heretic engine
**Description.** `src/heretic` remains behaviourally identical to the audited upstream tree. The only permitted deviations are the distribution-alias shim required by the package rename, its three call sites, and dependency-skip guards in upstream tests — all recorded in ADR-011.
**Motivation.** The engine is the only component that performs model modification; its determinism and plugin contracts underpin every platform guarantee. SPEC REQ-01.
**Acceptance criteria.**
- `git diff aecd4a8 -- src/heretic` shows only import/call-site substitutions in `main.py`, `system.py`, `utils.py`; `git status` shows only the new `_distribution.py` under `src/heretic`. No algorithm, config key, plugin contract or serialization change.
- `python -c "import heretic, heretic.main, heretic.config, heretic.plugin, heretic.scorer, heretic.modifier"` succeeds in ENGINE.
- `[project.scripts]` still contains `heretic = "heretic.main:main"`.
- Every `src/heretic/*.py` retains the AGPL SPDX header and upstream copyright line.
- Upstream unit tests pass in ENGINE and skip (not error) in LIGHT.
**Dependencies.** None.
**Risks.** Future upstream merges conflict with the shim; scope creep into engine algorithms.
**Verification.** `git diff --stat aecd4a8 -- src/heretic` (3 files, shim + 3 call sites); `heretic --help` → rc 0 (ENGINE); upstream unittest → 47 tests OK; LIGHT run → 5 upstream modules skipped with `could not import 'torch'`.
**Status.** **Implemented** — documented engine exception (ADR-011); no algorithmic change.

### FR-002 — Engine must start under the `fsociety-ai` distribution
**Description.** Version/origin resolution works when the installed distribution is `fsociety-ai` (no `heretic-llm` metadata). `heretic` prints its banner and reaches the CLI/settings stage.
**Motivation.** SPEC AC-02 requires a working console script; without it the engine is importable but unusable.
**Acceptance criteria.**
- Installed wheel (distribution `fsociety-ai`, version 3.0.0) → `heretic --help` exits `0`.
- `_distribution.get_version()` resolves `heretic-llm` first, then `fsociety-ai`; both directions preserve `PackageNotFoundError` when neither exists.
- `reproduce.json`/`requirements.txt` keys are unchanged (still `heretic-llm`), so historical manifests keep their meaning.
- Regression coverage exists for the alias resolution.
**Dependencies.** FR-001, ADR-011.
**Risks.** A second rename would repeat the incident; the alias list must stay documented.
**Verification.** `/tmp/opencode/engine-env/bin/heretic --help` → usage output, rc 0; `importlib.metadata` reports only `fsociety-ai`; `tests/unit/test_engine_adapter.py` covers engine status/version paths.
**Status.** **Implemented**.

### FR-003 — Engine configuration generation contract
**Description.** Generated Heretic 2.x TOML is valid and semantically correct: keys exist in `heretic.config.Settings`, reserved keys cannot be overridden, values are TOML-representable, and generation length is its own setting (`max_response_length`), never derived from a context length.
**Motivation.** The adapter is the only safe way to drive the engine; key/semantic mismatch silently changes runs.
**Acceptance criteria.**
- Generated keys ⊆ `heretic.config.Settings.model_fields`; unknown `extra` keys rejected with a typed error.
- `max_response_length` is explicit (default 100), independent of any context/sequence setting; no `sequence_length` is emitted.
- `enable_shell=false`, `seed` recorded, `dtypes` a valid fallback ladder, `device_map`/`quantization` validated.
- Golden/contract test pins emitted document keys and semantics.
**Dependencies.** FR-001.
**Risks.** Upstream renames a setting; contract test is the guard.
**Verification.** `tests/unit/test_engine_adapter.py` (16 test functions) in LIGHT/ENGINE; `grep sequence_length src/fsociety/core/engine.py` → only `max_response_length` handling.
**Status.** **Implemented**.

### FR-004 — Sanitized engine execution
**Description.** The engine runs as a subprocess with `shell=False`, an environment allowlist, a configurable timeout, a working directory tied to the config, rejection of policy-relaxing flags and shell metacharacters, and truthful unavailability errors.
**Motivation.** Prevents injection through model ids/arguments and bypass of `trust_remote_code` policy.
**Acceptance criteria.**
- `--trust-remote-code`/`--trust_remote_code` rejected before spawning.
- Control characters, shell metacharacters and `..` segments rejected in model references.
- Env contains only allowlisted keys plus `PYTHONUNBUFFERED`, `TOKENIZERS_PARALLELISM`, offline flags.
- Non-zero exit logged with truncated stderr; missing deps raise `EngineUnavailableError` with hint.
**Dependencies.** FR-003.
**Risks.** Interactive engine prompts can stall a subprocess until timeout; unattended flows remain undefined (see §6).
**Verification.** `tests/unit/test_engine_adapter.py`; `fsociety engine status --json` in LIGHT (rc 1, structured error) and ENGINE.
**Status.** **Implemented** for invocation hardening; the *unattended end-to-end* use case remains unproven (§6, item 7).

## 1.2 Hardware and resource intelligence

### FR-005 — Hardware detection without assuming OS implies capability
**Description.** Detect CPU, RAM, swap and accelerators (NVIDIA, ROCm, MPS, XPU) from real sources, distinguishing driver-level from runtime availability; never raise when a probe is missing.
**Motivation.** SPEC REQ-02/AC-04..06; planning is meaningless if hardware is guessed.
**Acceptance criteria.**
- CPU-only machine → `gpus=[]`, `backends.cpu=true`, explanatory warnings.
- Missing `nvidia-smi` → no exception, no GPU entries.
- Mocked NVIDIA output → CUDA GPU with measured memory/compute capability.
- Mocked MPS on Darwin → one unified-memory accelerator with RAM as budget.
- Probes injectable (`runner`, `torch_probe`) so tests never touch real hardware.
**Dependencies.** None (psutil/cpuinfo optional).
**Risks.** Vendor tool output drift silently yields no GPUs.
**Verification.** `tests/unit/test_foundation.py` hardware tests; `fsociety hardware --json` measured (`cpu.source=measured`, `detection_mode=measured`, 2 warnings, `torch.installed=false`, fingerprint present).
**Status.** **Implemented**.

### FR-006 — Hardware provenance contract
**Description.** Reported hardware values carry provenance and, where applicable, confidence; the profile has a stable fingerprint excluding volatile values.
**Motivation.** SPEC AC-04; estimates must be attributable and fingerprints comparable.
**Acceptance criteria.**
- `HardwareProfile.fingerprint` deterministic for equal capabilities, insensitive to free memory/time.
- `GpuInfo` exposes `source` + `confidence`; `CpuInfo` exposes `source`.
- `detection_mode=measured` only when RAM and CPU model were read.
**Dependencies.** FR-005.
**Risks.** `CpuInfo` still lacks a per-field `confidence`; strict per-field AC-04 reading is not fully met.
**Verification.** `tests/unit/test_foundation.py::test_hardware_fingerprint_stable_and_volatile_free`; model inspection of `core/models.py:63-71`.
**Status.** **In progress** — provenance yes, CPU confidence field absent.

### FR-007 — Analytical resource estimation
**Description.** Estimate weights, KV cache, activations, overhead and fragmentation from model metadata, with assumptions and `kind="estimate"`.
**Motivation.** SPEC REQ-03/AC-07; prevents unusable downloads and gives the UI honest numbers.
**Acceptance criteria.**
- Pure function of `(ModelInfo, EstimationRequest, HardwareProfile)`.
- `kind` always `"estimate"`; assumptions non-empty and include the KV formula used.
- Weights scale with precision; KV/activations scale with batch and sequence.
- Missing parameter count → `ValidationError` with a hint.
- No hardware → `fits=None`.
**Dependencies.** FR-005, FR-009.
**Risks.** Constants uncalibrated (no measured calibration on this host).
**Verification.** `tests/unit/test_foundation.py` estimator tests (7); `tests/unit/test_planner.py` (16).
**Status.** **Implemented**.

### FR-008 — Actionable alternatives when resources are insufficient
**Description.** When an estimate does not fit, return ranked alternatives with resulting footprint; when nothing fits, say so and list the levers.
**Motivation.** SPEC AC-08.
**Acceptance criteria.**
- `fits=False` → at least one alternative or the explicit "smallest mode does not fit" message.
- Recommended mode re-estimated and reported with its footprint.
- No hardware → recommendations `None`, not guessed.
**Dependencies.** FR-007.
**Risks.** Recommending quantization where bitsandbytes is absent; reported as option, not promise.
**Verification.** `tests/unit/test_foundation.py::test_estimate_*`; `tests/unit/test_planner.py::test_plan_70b_does_not_fit_and_explains_why`.
**Status.** **Implemented**.

## 1.3 Model management

### FR-009 — Torch-free model discovery and inspection
**Description.** Discover local model directories and HF cache snapshots; extract architecture metadata from `config.json`, tokenizer files, safetensors headers and GGUF metadata without importing torch.
**Motivation.** SPEC REQ-04/AC-10; management must work on CPU-only control hosts.
**Acceptance criteria.**
- `ModelManager.inspect(path)` returns `ModelInfo` with parameter count, layers, hidden size, heads, KV heads, dtype, quantization, formats, license.
- Safetensors headers parsed/validated without materializing tensors; sharded indexes handled.
- Corrupt/partial files → warnings, not exceptions.
- Scans respect depth/entry limits, skip symlinks, hidden dirs and HF cache internals.
- No `torch` in `sys.modules` after inspection.
**Dependencies.** FR-005.
**Risks.** Parameter-count heuristics may differ from the real model.
**Verification.** `tests/unit/test_model_manager.py` (19 test functions) in both profiles.
**Status.** **Implemented**.

### FR-010 — Integrity manifests and verification
**Description.** Generate/consume SHA-256 manifests and verify them, reporting missing/size/hash mismatches with a non-zero exit code.
**Motivation.** SPEC AC-11; provenance requires immutability checks.
**Acceptance criteria.**
- `fsociety models verify` exits 0 on match, non-zero on mismatch, printing each mismatch.
- Manifest paths relative, normalized, traversal-safe.
- Verification never writes into the verified directory.
- `ModelValidation.ok` true only when all listed files exist and match.
**Dependencies.** FR-009.
**Risks.** Hashing multi-GB files is slow; needs progress and size policy.
**Verification.** `tests/unit/test_model_manager.py`; CLI `models verify` covered by `tests/integration/test_cli.py`.
**Status.** **Implemented**.

### FR-011 — Revision-pinned downloads with resolved commits
**Description.** Any HF download either pins an explicit revision or returns the resolved commit; offline mode fails actionably without network access.
**Motivation.** SPEC AC-12; reproduction depends on immutable identifiers.
**Acceptance criteria.**
- `download_snapshot` records the resolved commit; returned path is `snapshots/<commit>`.
- Full-weight download requires `allow_patterns` or `confirm_weights=True`.
- `offline=True` → typed error, no Hub contact.
- Repo ids/patterns validated (no schemes, traversal, absolute patterns).
**Dependencies.** FR-009.
**Risks.** Gated repos need credentials; `HF_TOKEN` is intentionally excluded from subprocess allowlists (SEC-012).
**Verification.** `tests/unit/test_model_manager.py` (offline and download tests); `fsociety models download` in LIGHT for a metadata-only pattern.
**Status.** **Implemented**.

## 1.4 Optimization

### FR-012 — Real, persisted hyperparameter optimization
**Description.** Optuna TPE (seeded) over a declarative search space, every trial persisted (params, values, state, timing) in SQLite, progress reporting, cooperative cancellation.
**Motivation.** SPEC REQ-05/AC-13.
**Acceptance criteria.**
- Quadratic objective converges to its optimum (test).
- Every completed/failed trial stored with params/values/names/duration/created_at.
- Cancellation stops between trials with `status=cancelled`.
- Missing Optuna raises `EngineUnavailableError` (no stub optimizer).
- Non-finite objective values rejected.
**Dependencies.** FR-021, FR-014.
**Risks.** Optuna upgrades can change sampling; version recorded in provenance.
**Verification.** `tests/unit/test_optimizer.py` (18 test functions) in ENGINE; LIGHT skips via the `engine` marker path.
**Status.** **Implemented**.

### FR-013 — Deterministic search
**Description.** Identical seed + space + objective produce identical trial suggestions and stored parameters.
**Motivation.** SPEC AC-15; comparisons are meaningless otherwise.
**Acceptance criteria.**
- Two same-seed runs yield identical ordered parameter sets; different seeds differ.
- Sampler settings recorded in experiment metadata.
- Grid-size computation deterministic and documented.
**Dependencies.** FR-012.
**Risks.** Sampler internals change across Optuna versions.
**Verification.** `tests/unit/test_optimizer.py::test_identical_seed_and_space_reproduce_the_same_trials`.
**Status.** **Implemented**.

### FR-014 — Constraint validation and execution planning
**Description.** Reject configurations exceeding detected memory with explanations; produce an execution plan (device, map, dtype, quantization, batch, sequence length, footprint, warnings).
**Motivation.** SPEC AC-14.
**Acceptance criteria.**
- Structured problems (not booleans) for memory, context > model limit, unsupported precision, batch ≤ 0.
- Plans carry estimate semantics and warnings when hardware is unknown.
- Search-space recommendation narrows bounds for a concrete model and collapses batch when oversized.
- Merging user params rejects reserved/invalid keys.
**Dependencies.** FR-007, FR-005.
**Risks.** Multi-GPU topology advice may be wrong; surfaced as warning.
**Verification.** `tests/unit/test_planner.py` (16 tests).
**Status.** **Implemented**.

## 1.5 Evaluation

### FR-015 — Reproducible evaluation with bundled suites and real backends
**Description.** Load versioned suites, run against a real backend, grade deterministically, persist a report with model id, suite hash, seed, hardware fingerprint, config hash, software versions and timings.
**Motivation.** SPEC REQ-06/AC-16.
**Acceptance criteria.**
- Suite registry validates schema + manifest hash; tampering detected.
- 7 bundled suites / 72 items load (measured); provenance and limitations declared.
- Reports record all provenance fields; missing hardware → warning, never a fabricated fingerprint.
- Reports written atomically (JSON + Markdown), reload equal to in-memory object.
**Dependencies.** FR-005, FR-016, FR-021.
**Risks.** Suites are smoke suites, not official benchmarks.
**Verification.** `tests/unit/test_evaluation.py` (27 test functions); `fsociety evaluate suites --json` (7 suites, 72 items).
**Status.** **Implemented**.

### FR-016 — Deterministic graders
**Description.** Pure graders mapping `(BenchmarkItem, response|None)` to `(score ∈ [0,1], passed, details)` with a grader version; never raise into the runner.
**Motivation.** AC-16/AC-17 depend on stable grading.
**Acceptance criteria.**
- 8 registered graders: exact_match, contains_expected, numeric, python_syntax, instruction_format, spanish_language, defensive_cyber_rubric, abstention.
- `grade_with` clamps scores, injects grader/version, converts exceptions into `error=grader_exception:*` with score 0.
- Deterministic for identical inputs; `None`/empty responses handled explicitly.
**Dependencies.** None.
**Risks.** Heuristic graders can be gamed; limitations documented.
**Verification.** `tests/unit/test_graders.py`.
**Status.** **Implemented**.

### FR-017 — Provenance-complete reports
**Description.** Every report is self-describing: schema version, suite id+hash, grader version, backend label, model id, seed, config hash, hardware fingerprint/profile, software versions, timings, peak RSS, per-item results, aggregate/category metrics.
**Motivation.** AC-16, NFR-002.
**Acceptance criteria.**
- `config_hash` stable across key ordering.
- Markdown output contains no raw secrets (redaction applied on write).
- `load_report(write_report(r))` round-trips.
- Reports stored under workspace `reports/` and indexed by report id.
**Dependencies.** FR-015, SEC-007.
**Risks.** Large per-item payloads need API pagination.
**Verification.** `tests/unit/test_evaluation.py` (provenance assertions incl. `hardware_fingerprint`, `config_hash`, round-trip); `tests/security/test_report_redaction.py`.
**Status.** **Implemented**.

### FR-018 — Comparability and regression detection
**Description.** Compare reports only when suite hash, grader version and model conditions match materially; otherwise return `comparable=false` with reasons.
**Motivation.** SPEC AC-17; silent cross-condition comparison is how benchmark dashboards lie.
**Acceptance criteria.**
- Mismatched suite hash/grader version/model → `incomparable` + reason.
- Comparable reports produce metric deltas with direction, regression flags, threshold.
- Verdict ∈ {improved, regressed, mixed, equivalent, incomparable}.
**Dependencies.** FR-017.
**Risks.** Default threshold (2%) may mislabel small deltas; threshold surfaced.
**Verification.** `tests/unit/test_regression.py`; `fsociety evaluate compare` in integration tests.
**Status.** **Implemented**.

### FR-019 — No fabricated outputs in production paths
**Description.** Production backends return real model output only; scripted/test-double backends exist for tests and are explicitly labelled.
**Motivation.** SPEC AC-18, NFR-006.
**Acceptance criteria.**
- No fixture responses under `src/fsociety`.
- A scripted backend cannot be selected without being labelled in the report.
- Reports produced with a test double are identifiable from the report alone.
**Dependencies.** FR-015.
**Risks.** Convenience mocks creeping into `src/`.
**Verification.** `rg "CallableBackend|test double" src tests`; evaluation tests assert backend labels; grep gate in CI (NFR-006).
**Status.** **Implemented**.

### FR-020 — lm-eval integration
**Description.** Adapter builds an argv (no shell), runs the harness, parses the newest results file, records harness/task provenance, enforces trust policy.
**Motivation.** Users expect standard tasks; the platform must not reimplement or fake their scores.
**Acceptance criteria.**
- Task names and numeric options validated against allowlists/ranges.
- argv only, never a shell string.
- Missing `lm_eval` → actionable error/unavailable result.
- Provenance includes harness version, tasks, limit, batch, device.
**Dependencies.** FR-019.
**Risks.** Harness output-format drift; long runtimes; task assets need network.
**Verification.** `tests/unit/test_evaluation.py` (`test_lm_eval_unavailable_error_is_actionable`, `test_lm_eval_build_command_is_a_real_subprocess_argv`); lm-eval 0.4.13 present in ENGINE.
**Status.** **Implemented** (adapter); no full harness run was executed in this pass.

## 1.6 Persistence and jobs

### FR-021 — Durable experiment store
**Description.** Persist experiments, trials, jobs, events and artifacts in SQLite (WAL) with schema versioning, Pydantic-validated rows and typed conflicts.
**Motivation.** SPEC REQ-07/AC-19/AC-20.
**Acceptance criteria.**
- Round-trip preserves all fields for every record type.
- Concurrent event writes do not corrupt the DB or lose rows.
- Unknown fields rejected at the model boundary.
- Experiments carry seed, config hash, suite hash, hardware fingerprint, software versions.
- Unsupported schema version → `ConflictError` with migration hint.
**Dependencies.** FR-005.
**Risks.** Single connection + global lock; no migration tool yet.
**Verification.** `tests/unit/test_foundation.py` store tests (round-trip, concurrency, unknown field).
**Status.** **Implemented** — concurrency evidence remains a 2-thread smoke test, not a stress test.

### FR-022 — Job manager with cancellation and restart semantics
**Description.** Background jobs with queued/running/succeeded/failed/cancelled states, progress, cooperative cancellation, startup recovery of interrupted jobs.
**Motivation.** SPEC AC-29.
**Acceptance criteria.**
- States/progress persisted and readable via API.
- Cancel honoured between trials/items; cancelled ≠ failed.
- Startup marks left-over `queued`/`running` jobs failed with an explanation.
- Shutdown signals cancellation and cancels queued futures; an in-flight job may delay exit (documented).
**Dependencies.** FR-021.
**Risks.** Backend calls cannot be interrupted mid-call; granularity documented.
**Verification.** `tests/unit/test_jobs.py` (4 test functions); `tests/api/test_api.py::test_optimization_job_runs_and_persists`.
**Status.** **Implemented**.

## 1.7 Plugin ecosystem

### FR-023 — Versioned plugin API with discovery and validation
**Description.** `PluginBase` v1, kinds, metadata, discovery (builtin, entry points, workspace), validation with explicit errors, duplicate handling, audit logging.
**Motivation.** SPEC REQ-08/AC-21.
**Acceptance criteria.**
- Wrong `api_version` or base class rejected with the expected contract named.
- Invalid plugins surface as `valid=false` with errors; discovery never crashes.
- Duplicates ignored and audited.
- Builtins discoverable with metadata (measured: 7).
**Dependencies.** SEC-009.
**Risks.** Entry-point metadata is untrusted input.
**Verification.** `tests/unit/test_plugins.py` (31 test functions) + `tests/fixtures/plugins/*`; `fsociety plugins list --json`.
**Status.** **Implemented**.

### FR-024 — Plugin isolation
**Description.** Execute plugins in a subprocess with env allowlist, timeout, output cap, POSIX rlimits, process-group kill and optional network namespace; record failures without crashing the host.
**Motivation.** SPEC AC-22.
**Acceptance criteria.**
- Raising plugin → structured error, host unaffected.
- Looping plugin → killed at timeout, `PluginTimeoutError` recorded.
- Flooding plugin → truncated at cap, bounded memory.
- Missing `unshare` reported truthfully.
**Dependencies.** FR-023.
**Risks.** Process isolation is not a sandbox (shared kernel/user, filesystem reachable).
**Verification.** `tests/unit/test_plugins.py` with adversarial fixtures (`crashing_scorer`, `looping_scorer`, `noisy_scorer`, `import_failure`, `invalid_api_version`, `missing_dependency`).
**Status.** **Implemented** — with the documented isolation limitation (see §6, item 6).

### FR-025 — Example plugins usable as templates
**Description.** Ship importable example scorers across domains (behavioral regression, code quality, defensive security, hallucination, model integrity, reasoning, Spanish language), documented as examples.
**Motivation.** SPEC AC-23.
**Acceptance criteria.**
- All examples import and validate against `PluginBase` v1.
- Each declares metadata and a deterministic scoring function.
- At least one example exercised end-to-end through the isolation worker in tests.
**Dependencies.** FR-023.
**Risks.** Example scores mistaken for benchmark validity.
**Verification.** `tests/unit/test_plugins.py` (import/validate/determinism/isolation); `fsociety plugins validate`.
**Status.** **Implemented**.

## 1.8 Interfaces

### FR-026 — FastAPI v1 surface
**Description.** `/api/v1` endpoints for system, hardware, models, optimization jobs, evaluation jobs, experiments, plugins, exports, security and settings; SPA served when a build exists.
**Motivation.** SPEC REQ-10/AC-28..30.
**Acceptance criteria.**
- `create_app()` constructs; `app.openapi()` returns the documented paths.
- Public: `/api/v1/health`, `/api/v1/version`; everything else authenticated.
- Unknown routes return the structured 404 envelope.
- App starts with no torch installed.
**Dependencies.** FR-021, FR-022, SEC-003..006.
**Risks.** A single bad return annotation blocks the whole app (historical incident, now covered by `test_openapi_schema`).
**Verification.** `tests/api/test_api.py` (14 test functions) in LIGHT; manual `serve` on 127.0.0.1:8127 → `/api/v1/health` 200 (`status=ok`, `engine=unavailable`), `/api/v1/version` 200 (`engine_available=false`), `/` 200 serving `dist/index.html` (696 bytes), `/api/v1/models` 200 from loopback.
**Status.** **Implemented**.

### FR-027 — API authentication and traffic controls
**Description.** API-key auth (`X-API-Key` or Bearer), loopback trust, fail-closed remote access, rate limiting, body-size cap, TrustedHost, CORS allowlist.
**Motivation.** SPEC AC-26, SEC-003..006.
**Acceptance criteria.**
- Non-loopback without a configured key → 403; missing key → 401; wrong key → 401; constant-time comparison.
- Loopback allowed unless `api_require_key_for_loopback`.
- Rate limit → 429 with `Retry-After` and rate headers.
- Body over cap → 413; wildcard CORS rejected at config time.
**Dependencies.** FR-026.
**Risks.** Per-process limiter; proxy deployments must configure trusted hosts explicitly.
**Verification.** `tests/api/test_api.py` (auth, headers, structured 404); `tests/unit/test_foundation.py::test_config_rejects_wildcard_cors`; manual header inspection (CSP, nosniff, DENY, referrer, permissions, COOP/CORP).
**Status.** **Implemented**.

### FR-028 — Structured errors and OpenAPI
**Description.** Stable `code`, message, optional details/hint, HTTP mapping, request id, no stack traces; uniform envelope for validation errors.
**Motivation.** SPEC AC-28/AC-30, NFR-004.
**Acceptance criteria.**
- Every `FSocietyError` subclass defines `code`/`http_status`.
- Envelope shape constant: `{error: {code, message, details?, hint?, request_id}}`.
- Pydantic/FastAPI validation errors converted to the same envelope.
- OpenAPI operation ids unique and stable.
**Dependencies.** FR-026.
**Risks.** FastAPI default handlers bypassing the envelope.
**Verification.** `tests/api/test_api.py::test_openapi_schema`, `::test_unknown_api_route_returns_structured_error`.
**Status.** **Implemented**.

### FR-029 — CLI surface
**Description.** `fsociety` covers version, doctor, hardware, models, optimize, evaluate, experiments, plugins, security, export, serve, config and engine, with `--json` accepted globally and after subcommands.
**Motivation.** SPEC AC-34.
**Acceptance criteria.**
- `fsociety <cmd> --help` exits 0 for every group.
- Read-only commands work in LIGHT.
- `--json` parseable, no Rich markup, accepted in both positions.
- Exit codes 0/1/2/130.
- `doctor` reports engine/dashboard/auth/trust/plugin status truthfully.
**Dependencies.** FR-005, FR-009, FR-026, FR-032.
**Risks.** Flag-position regressions (fixed; covered by CLI tests).
**Verification.** `tests/integration/test_cli.py` (7 test functions); manual `--json` after subcommand for `version`, `hardware`, `models list`, `security policy`, `security findings`, `evaluate suites`, `plugins list`, `config show`, `experiments list` → rc 0 with JSON; `engine status --json` → rc 1 with structured error (engine absent).
**Status.** **Implemented**.

### FR-030 — Dashboard with real data only
**Description.** React/TS operator console with ten sections, design tokens from `.factory/ARCHITECTURE.md` §5, loading/empty/error states, responsive at 375/768/1440, accessible.
**Motivation.** SPEC REQ-11/AC-31..33.
**Acceptance criteria.**
- `npm run typecheck` and `npm run build` succeed; `dist/` produced.
- Ten routes reachable and linked from navigation.
- API unreachable → explicit error with retry; no mock metrics.
- Every async view has skeleton/empty/error states.
- No palette literals outside tokens.
**Dependencies.** FR-026.
**Risks.** Environment: no Node on this host, so the build is verified by artifact + external browser run, not re-run here.
**Verification.** `frontend/dist` exists (2.7 MB, `index.html` + `assets/`); 12 page components and 10 nav routes in `frontend/src/App.tsx`; served SPA returned 200 by the API; typecheck/build/Lighthouse (a11y 1.0, no console errors) reported by the orchestrator's UI pass.
**Status.** **Implemented** (build artifact + external verification; rebuild **skipped (environment)** on this host).

## 1.9 Export, security tooling, delivery

### FR-031 — Verifiable export bundles and GGUF boundary
**Description.** Export bundles with a manifest (files, sizes, SHA-256, metadata) and verification; GGUF treated as metadata boundary without claiming conversion.
**Motivation.** Artifact provenance and honest capability reporting.
**Acceptance criteria.**
- Bundle creation writes a manifest and an artifact record.
- Verification detects modified/missing/extra files.
- GGUF requests either delegate explicitly or fail with `ExportError`.
- Bundle paths jail-checked.
**Dependencies.** FR-010, SEC-001.
**Risks.** Manifest drift after hashing; atomic writes required.
**Verification.** `tests/unit/test_exporters.py`; `fsociety export bundle` in integration tests.
**Status.** **Implemented**.

### FR-032 — SBOM and dependency audit
**Description.** CycloneDX SBOM from installed distributions; `pip-audit` when available, reported unavailable otherwise.
**Motivation.** SPEC REQ-09 supply chain.
**Acceptance criteria.**
- SBOM contains only installed components with purls/versions.
- Missing `pip-audit` → `available=false` with reason.
- No secrets/environment values embedded.
**Dependencies.** None.
**Risks.** Auditing shell-out must stay `shell=False` with timeout.
**Verification.** `tests/security/test_sbom.py`; `fsociety security sbom` (LIGHT).
**Status.** **Implemented** — audit availability reported, not assumed.

### FR-033 — Platform configuration
**Description.** TOML + `FSOCIETY_*` configuration with documented precedence, validation, redaction and safe starter file.
**Motivation.** Operability and secret hygiene.
**Acceptance criteria.**
- Precedence defaults < TOML < env < overrides tested.
- Wildcard CORS and malformed trusted repos rejected.
- `redacted()` never includes the API key; `api_key_configured` present.
- Missing `--config` → typed error with hint.
- `config init` writes a file that loads unchanged.
**Dependencies.** SEC-008.
**Risks.** `extra="ignore"` hides TOML typos.
**Verification.** `tests/unit/test_foundation.py` config tests (6); `fsociety config init`/`config show --json`.
**Status.** **Implemented**.

### FR-034 — Packaging and install profiles
**Description.** Wheel contains both packages, supports a light (platform-only) install and a full engine install; commands documented.
**Motivation.** SPEC AC-34.
**Acceptance criteria.**
- `uv build` produces wheel + sdist containing `heretic/` and `fsociety/`.
- Both console scripts declared.
- LIGHT install → `fsociety` works and `import fsociety` needs no torch; `doctor` passes with engine warnings.
- Full install → `heretic --help` works and `engine status.available=true`.
**Dependencies.** FR-002.
**Risks.** Light install via `--no-deps` is fragile; `pydantic` is still only transitive (P3-1).
**Verification.** `uv build --out-dir /tmp/opencode/dist-check` → `fsociety_ai-3.0.0-py3-none-any.whl` (319 KB) with 22 `heretic/` + 81 `fsociety/` entries, 8 benchmark JSONs (7 suites + manifest), and `entry_points.txt` declaring `fsociety` + `heretic`; installed-wheel `heretic --help` rc 0; `doctor` and `serve` exercised from source in LIGHT.
**Status.** **Implemented** (wheel contents and engine console script verified; clean-venv `fsociety` console script reported by the orchestrator, not re-installed in this pass).

### FR-035 — Documentation set
**Description.** README, install/quickstart, architecture, CLI/API/config reference, plugin/optimizer/benchmark/security guides, troubleshooting, migration notes and changelog.
**Motivation.** SPEC AC-36 and AGPL expectations for a network service.
**Acceptance criteria.**
- Every CLI command appears in the CLI reference with an example.
- Every API path appears in the API reference.
- Benchmark methodology states suites are smoke suites and explains comparability.
- Migration covers 1.x→2.x and the fork packaging change.
**Dependencies.** FR-029, FR-026.
**Risks.** Drift from `--help`/OpenAPI; generate where possible.
**Verification.** `docs/guides/{INSTALLATION,CLI,API,CONFIGURATION,PLUGINS,OPTIMIZATION,BENCHMARKING,TROUBLESHOOTING,MIGRATION}.md` (1,238 lines total); `README.md` rewritten (9,727 bytes); `CHANGELOG.md`, `SECURITY.md`, `CONTRIBUTING.md`, `.env.example` present.
**Status.** **Implemented**.

### FR-036 — Final delivery report
**Description.** `docs/reports/FINAL_PROJECT_REPORT.md` lists every requirement, status, executed commands, passed/failed/skipped tests, security findings, performance evidence, compatibility notes, limitations and roadmap.
**Motivation.** SPEC AC-36; the exit gate requires honest evidence.
**Acceptance criteria.**
- Every AC maps to a status with a command or an explicit skip reason.
- Skipped items name the prerequisite.
- Security section links to a `.factory/SECURITY_AUDIT.md` with zero open CRITICAL/HIGH and an explicit approval state.
**Dependencies.** All.
**Risks.** Reporting optimism; mitigate with command transcripts.
**Verification.** Manual review against this document and `.factory/SPEC.md`. Measured: report present (16,182 bytes, 322 lines) and cross-checked on 2026-11-23T11:20Z — it correctly marks Docker/GPU/modification runs as unverified, states test counts from its own run (341/368/47), and links the audit.
**Status.** **Implemented** — report exists, test counts refreshed (473 LIGHT / 500 ENGINE), and the security audit is **APPROVED** (0 CRITICAL/0 HIGH). Docker/GPU remain environmental gaps recorded in the report.

---

# 2. Non-functional requirements

### NFR-001 — Import isolation
Core platform modules must import without `torch`, `transformers`, `optuna`, `fastapi` or Node.
**Verification.** 15 modules imported in fresh processes (initial audit §11); `tests/unit/test_foundation.py::test_core_import_does_not_load_torch`; LIGHT suite runs with no engine packages installed.
**Status.** **Implemented**.

### NFR-002 — Reproducibility envelope
Every experiment/report records seed, config hash, suite hash, hardware fingerprint, software versions, timestamps, artifact hashes.
**Verification.** `tests/unit/test_evaluation.py` asserts `hardware_fingerprint`, `config_hash`, suite hash and round-trip; `tests/unit/test_optimizer.py::test_hardware_fingerprint_falls_back_to_settings`.
**Status.** **Implemented**.

### NFR-003 — Secret hygiene
No secret in repository, logs, API responses, reports, manifests or error messages; redaction on write and on response.
**Verification.** `tests/unit/test_foundation.py::test_redaction*`, `::test_audit_logger_redacts`; `tests/security/test_report_redaction.py`; secret scan of 293 files → 2 matches, both synthetic fixtures in `tests/unit/test_foundation.py`, 0 in `src/`.
**Status.** **Implemented**.

### NFR-004 — No silent failures
Every caught exception recovers with a recorded warning/event or re-raises as a typed error with hint; blanket swallowing forbidden outside commented best-effort cleanup.
**Verification.** Code review of `except` blocks; no automated gate yet.
**Status.** **In progress** — deliberate best-effort handlers remain (log rotation, persistence of logs), each commented.

### NFR-005 — Backward compatibility
See §4 (BC-001..BC-007).
**Status.** **Implemented with the documented engine exception** (ADR-011).

### NFR-006 — No placeholders or mocks in production paths
No `TODO`/`FIXME`/stub returns/canned model output under `src/`.
**Verification.** `grep -rIn 'TODO\|FIXME\|XXX\|NotImplementedError' src/fsociety` → 0; test doubles live under `tests/`.
**Status.** **Implemented**.

### NFR-007 — Evidence-based performance claims
No throughput/memory/benchmark claim without a measured artifact; unavailable hardware reported as skipped.
**Verification.** `docs/reports/FINAL_PROJECT_REPORT.md` §7 lists only measured values (suite durations, bundle sizes, SBOM size, 4-trial job) and explicitly states that no model throughput/VRAM/latency benchmark was run; this document and §10 keep measured/estimated/skipped distinct; estimator outputs stay `kind="estimate"`.
**Status.** **Implemented**.

### NFR-008 — Determinism
Greedy decoding in the engine; seeded Python/NumPy/PyTorch/Optuna; pure graders; canonical JSON hashing; no wall-clock/RNG in report content beyond recorded timestamps.
**Verification.** `tests/unit/test_optimizer.py::test_identical_seed_and_space_reproduce_the_same_trials`; grader determinism tests; canonical suite hashing.
**Status.** **Implemented**.

### NFR-009 — Portability and graceful skips
Python 3.10–3.14 supported; tests needing torch/Optuna/Node skip with a reason, never fail, on LIGHT.
**Verification.** LIGHT: `473 passed, 6 skipped` (5 upstream torch modules + 1 trust-policy guard that requires Heretic to be absent). ENGINE: `500 passed, 3 skipped`. Upstream: `47 tests OK`. CLI matrix in CI: 3.10/3.12/3.14.
**Status.** **Implemented**.

### NFR-010 — Offline operation
`offline=true` prevents Hub access with actionable errors; engine and plugin environments propagate offline flags.
**Verification.** `tests/unit/test_model_manager.py` offline tests; `core/engine.py` sets `HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE`.
**Status.** **Implemented**.

### NFR-011 — Concurrency and durability
SQLite WAL, serialized writes, atomic JSON writes, job state survives restart, no torn reports.
**Verification.** Store concurrency smoke test; `atomic_write_text` used by reporting/suites.
**Status.** **In progress** — crash-consistency (kill mid-write) untested; stress test absent.

### NFR-012 — Observability
Append-only redacted JSONL audit log with rotation; events persisted in SQLite; request ids in API responses; CLI hints.
**Verification.** `tests/unit/test_foundation.py::test_audit_logger_redacts`; `tests/api/test_api.py::test_events_endpoint_records_activity`; manual header/request-id inspection.
**Status.** **Implemented**.

### NFR-013 — Bounded resources
Timeouts on subprocess/network work; capped plugin output; limited scans and list endpoints; capped request bodies.
**Verification.** `plugins/isolation.py` bounded buffers/timeout; `api/middleware.py` body cap; `model_manager` scan limits; tests in `tests/unit/test_plugins.py` and API tests.
**Status.** **Implemented** for the enumerated surfaces; no global audit of every loop.

### NFR-014 — UI coherence, responsiveness, accessibility
Design tokens, loading/empty/error states, responsive 375/768/1440, AA contrast, visible focus, `aria-live` progress.
**Verification.** `frontend/dist` build artifact; 10 nav routes / 12 pages; orchestrator browser pass (Lighthouse a11y 1.0, no console errors, 10 routes against live API).
**Status.** **Implemented** (externally verified; rebuild **skipped (environment)** — Node absent here).

### NFR-015 — Licensing and attribution
AGPL-3.0-or-later preserved; upstream copyright/SPDX headers retained; fork modifications documented; network users offered source (AGPL §13) once distributed.
**Verification.** `LICENSE`, per-file SPDX headers including the new `_distribution.py`, `pyproject` authors/license, `SECURITY.md`/`README.md`.
**Status.** **In progress** — runtime product strings still reference heretic-project.org (intentional upstream banner), and the AGPL §13 source-offer wording is not yet in the delivered docs/report.

---

# 3. Security requirements

| ID | Requirement | Acceptance (testable) | Verification | Status |
| :--- | :--- | :--- | :--- | :--- |
| **SEC-001** | Path jail for every user-supplied path | Traversal, absolute paths and symlink escapes rejected | `tests/unit/test_foundation.py::test_safe_join_*` | **Implemented** |
| **SEC-002** | `trust_remote_code` deny-by-default | `never` denies; `allowlist` requires exact `org/name`; malformed ids rejected; engine flags refused | `tests/security/test_policy.py` trust tests; `fsociety security policy --json` (`mode=never`) | **Implemented** |
| **SEC-003** | API authentication | Remote without key → 403; no key → 401; bad key → 401; constant-time compare; loopback exempt unless configured | `tests/api/test_api.py` auth tests | **Implemented** |
| **SEC-004** | Rate limiting | Sliding window; 429 with `Retry-After`; remaining reported | `tests/api/test_api.py` rate-limit coverage | **Implemented** |
| **SEC-005** | Security headers and CORS allowlist | CSP, nosniff, DENY framing, referrer, permissions, COOP/CORP; wildcard rejected | `tests/api/test_api.py::test_security_headers_present`; manual header capture; `test_config_rejects_wildcard_cors` | **Implemented** — CSP allows inline styles and data-URI fonts per ADR-012 |
| **SEC-006** | Request size and host controls | Oversize body → 413; disallowed `Host` → 400 | `tests/api/test_api.py`; `api/middleware.py` | **Implemented** |
| **SEC-007** | Redaction everywhere | Audit log, API errors, on-disk reports/comparisons and CLI errors contain no token patterns | `tests/security/test_report_redaction.py`; `tests/unit/test_foundation.py` redaction tests | **Implemented** |
| **SEC-008** | No secrets committed | Regex scan → 0 matches in `src/`; no key/secret files | 293-file scan (2 synthetic fixtures, 0 in `src/`); `.env.example` only | **Implemented** |
| **SEC-009** | Plugin isolation boundary | Crash/timeout/output flood contained; env allowlist; process-group kill; limits documented | `tests/unit/test_plugins.py` + adversarial fixtures | **Implemented** — process-level, not a sandbox |
| **SEC-010** | Plugin permission model | Unknown permission rejected; `network` requires explicit grant; dangerous permissions warn | `tests/security/test_policy.py`; `tests/unit/test_plugins.py` | **Implemented** |
| **SEC-011** | Engine shell disabled | Platform configs set `enable_shell=false`; API never exposes shell | `core/engine.py:643`; `tests/unit/test_engine_adapter.py` | **Implemented** |
| **SEC-012** | Credential handling | API key `SecretStr`, never serialized; HF token never persisted to settings/artifacts; `HF_TOKEN` excluded from subprocess allowlists | `test_config_redacts_api_key`; `test_settings_redact_api_key`; allowlist inspection (`core/engine.py:92`, `plugins/isolation.py:48`) | **Implemented** |
| **SEC-013** | Supply chain | CycloneDX SBOM from installed dists; `pip-audit` only when present; availability reported | `tests/security/test_sbom.py`; `fsociety security sbom` | **Implemented** |
| **SEC-014** | Engine subprocess hardening | No shell, env allowlist, forbidden flags, meta-character rejection, timeout | `tests/unit/test_engine_adapter.py` | **Implemented** |
| **SEC-015** | No stack traces to clients | Envelope only; unexpected errors logged with request id | `tests/api/test_api.py::test_unknown_api_route_returns_structured_error` | **Implemented** |
| **SEC-016** | No arbitrary network fetches from user input | Model refs reject URLs/schemes; API never fetches arbitrary URLs | `tests/security/test_policy.py` model-reference tests; code review of `reproduce.load_reproduction_information` | **Implemented** |

**Accepted, documented risks** (must stay visible in `.factory/SECURITY_AUDIT.md`): engine plugin loading executes code by configuration; the engine's `trust_remote_code` prompt has no internal policy (platform boundary only); plugin isolation is process-level, not a container; rate limiting is per process; CSP permits inline styles/data fonts (ADR-012). `.factory/SECURITY_AUDIT.md` is **APPROVED** (0 CRITICAL / 0 HIGH; 1 accepted MEDIUM `sqlitedict`, 3 accepted LOW). The residuals above remain documented.

---

# 4. Backward-compatibility requirements

| ID | Contract | Must remain true | Verification |
| :--- | :--- | :--- | :--- |
| **BC-001** | `import heretic` and the `heretic` console script | Both resolve; the script runs under the renamed distribution | `python -c "import heretic"`; installed-wheel `heretic --help` rc 0 |
| **BC-002** | Heretic 2.x configuration keys | Existing `config.*.toml` load; plugin tables accepted via `extra="allow"` | `tests/test_config.py` (18); adapter key-contract test |
| **BC-003** | Plugin contracts | `Scorer`/`Modifier` subclasses, file/dotted loading, settings namespaces, `init(ctx)`, `reproducible` | `tests/test_plugin.py` (ENGINE); adapter/plugin compatibility tests |
| **BC-004** | Engine CLI semantics | Positional model shorthand, `--reproduce`, `--collect-reproducibles`, `HERETIC_CONFIG_FILE` | `heretic --help`; CLI parser tests |
| **BC-005** | Reproduction manifest | `reproduce.json` v4 accepted; v1–v3 rejected with the documented message | fixture-based engine tests; `tests/unit/test_engine_adapter.py` |
| **BC-006** | Licensing/attribution | AGPL notice, upstream author, SPDX headers, upstream URL in metadata | header/`pyproject` inspection |
| **BC-007** | Engine source drift | Only ADR-011's shim (`_distribution.py`, new), its three call sites and dependency-skip guards differ from `aecd4a8`; no algorithm/config/plugin/serialization change | `git status src/heretic` (1 new file) + `git diff aecd4a8 -- src/heretic` (3 modified files) |

---

# 5. Traceability — SPEC acceptance criteria

| AC | Requirement | Verification command / test | Status (2026-11-23, post-remediation) |
| :--- | :--- | :--- | :--- |
| AC-01 | FR-001, NFR-009 | `python -m unittest discover -s tests -p 'test_*.py'` | **Implemented** — 47 tests OK (ENGINE); skipped-not-errored in LIGHT |
| AC-02 | FR-001, FR-002, BC-001 | `import heretic`; `heretic --help` | **Implemented** — rc 0 under `fsociety-ai` |
| AC-03 | FR-001, BC-006 | header/LICENSE/pyproject inspection | **Implemented** — headers intact, shim carries the upstream notice |
| AC-04 | FR-005, FR-006 | `fsociety hardware --json` | **Implemented** — JSON with provenance; per-field CPU confidence still open (FR-006) |
| AC-05 | FR-005 | missing-tool probe test | **Implemented** |
| AC-06 | FR-005 | CPU/NVIDIA/MPS mocked tests | **Implemented** |
| AC-07 | FR-007 | estimator purity/`kind`/assumptions | **Implemented** |
| AC-08 | FR-008 | alternatives tests | **Implemented** |
| AC-09 | FR-007 | precision/batch scaling tests | **Implemented** |
| AC-10 | FR-009 | directory scan without torch | **Implemented** — 19 model-manager tests |
| AC-11 | FR-010 | `models verify` exit codes | **Implemented** |
| AC-12 | FR-011 | pinned revision / resolved commit | **Implemented** |
| AC-13 | FR-012 | optimizer suite | **Implemented** — 18 tests in ENGINE |
| AC-14 | FR-014 | planner suite | **Implemented** — 16 tests |
| AC-15 | FR-013 | identical-seed test | **Implemented** |
| AC-16 | FR-015, FR-017 | evaluation provenance tests | **Implemented** |
| AC-17 | FR-018 | comparability tests | **Implemented** |
| AC-18 | FR-019 | no-fabrication gate | **Implemented** |
| AC-19 | FR-021 | concurrent store writes | **Implemented** (smoke test; stress test absent) |
| AC-20 | FR-021, NFR-002 | experiment fingerprint/config hash | **Implemented** |
| AC-21 | FR-023 | invalid API version/base class rejected | **Implemented** |
| AC-22 | FR-024 | crash/timeout isolation | **Implemented** |
| AC-23 | FR-025 | examples import + unit-tested | **Implemented** |
| AC-24 | SEC-001 | path traversal/symlink tests | **Implemented** |
| AC-25 | SEC-002 | trust allowlist tests | **Implemented** |
| AC-26 | SEC-003 | remote-without-key refused | **Implemented** |
| AC-27 | SEC-007, NFR-003 | redaction tests incl. report persistence | **Implemented** |
| AC-28 | FR-026, FR-028 | Pydantic validation + structured errors | **Implemented** |
| AC-29 | FR-022, FR-026 | cancellable jobs; API without torch | **Implemented** — health 200 with `engine=unavailable` |
| AC-30 | FR-028 | `app.openapi()` test | **Implemented** |
| AC-31 | FR-030 | `npm run build` + typecheck | **Implemented** (artifact + external run); rebuild **skipped (environment)** |
| AC-32 | FR-030 | API-offline error state | **Implemented** (external browser verification) |
| AC-33 | FR-030 | 10 sections reachable | **Implemented** — 10 nav routes, 12 pages, served SPA |
| AC-34 | FR-034, FR-029 | clean-venv install + `doctor` | **Implemented** (reported clean install; `doctor` and wheel contents re-verified here) |
| AC-35 | FR-034 | Docker CPU image + health | **Skipped (environment)** — Dockerfile/compose exist, daemon absent; image build unverified |
| AC-36 | FR-035, FR-036 | final report accuracy | **Implemented** — report present and cross-checked; audit approval still pending (exit-gate caveat) |

Summary: **35 Implemented**, **1 Skipped (environment)** (AC-35), **0 Not started**. Open sub-items: FR-006 (CPU confidence), NFR-004 (automated no-silent-failure gate), NFR-011 (crash consistency), NFR-015 (AGPL §13 wording in delivery), and security-audit approval.

---

# 6. Explicitly not promised

1. **GPU or accelerator benchmark results.** No GPU/torch execution on this host; GPU-marked tests are skipped, never passed.
2. **Byte-for-byte reproducible weights.** Reproduction depends on identical hardware, driver, OS and full dependency closure; the engine documents and grades mismatches.
3. **Parity with official benchmarks.** Bundled suites are original smoke suites (72 items); lm-eval tasks are delegated, not reimplemented, and are not claimed equivalent across conditions.
4. **Arbitrary Python execution through the API.** The engine shell stays disabled; plugin execution is separate and bounded.
5. **Training or fine-tuning.** Behaviour modification only, via LoRA-space ablation.
6. **Sandbox-grade plugin isolation.** Process-level with env allowlist and resource caps; filesystem reachable, network only by explicit grant.
7. **Non-interactive end-to-end engine runs.** Trial/export/benchmark selection remains interactive; no unattended path is claimed or demonstrated.
8. **Multi-worker API deployments.** Rate limiting and the job manager are per process.
9. **GGUF conversion.** Metadata inspection and an explicit boundary only.
10. **Docker image build verification.** Dockerfile and compose exist, but no daemon is available on this host; AC-35 is reported as skipped.
11. **Publishing or pushing to any remote.** No release/tag/push without explicit authorization.

---

# 7. Open questions and decisions

| # | Question | Status | Resolution owner |
| :--- | :--- | :--- | :--- |
| Q-1 | Distribution identity: keep `fsociety-ai` or publish a `heretic-llm` alias distribution? | **Resolved** — alias shim inside the engine (ADR-011) | Architect (recorded) |
| Q-2 | May the platform touch `src/heretic` for the metadata fallback? | **Resolved** — yes, restricted to `_distribution.py` + 3 call sites (ADR-011) | Architect (recorded) |
| Q-3 | What is the single supported unattended engine invocation for API jobs? | **Open** — still interactive; not promised | Architect |
| Q-4 | Release Python targets? | **Resolved for CI** — platform 3.10/3.12/3.14, engine 3.12; classifiers still list 3.15 | Orchestrator |
| Q-5 | `--json` shape: global flag or per-subcommand? | **Resolved** — both accepted; covered by CLI tests | Architect (recorded) |
| Q-6 | Calibration data required before estimator accuracy claims? | **Open** — no measured calibration exists | Architect |

---

# 8. Implementation-vs-SPEC inconsistencies

### Resolved since the initial audit

1. **AC-01 skipping semantics** — upstream test modules now `importorskip("torch")`; LIGHT reports 5 skips, not errors; ENGINE runs 47 upstream tests.
2. **REQ-01 engine usability under the renamed distribution** — ADR-011 shim; installed-wheel `heretic --help` rc 0. ADR-007's "runtime compatibility stays" claim is now true (via ADR-011).
3. **AC-04 literal command** — `fsociety hardware --json` works; `--json` accepted in both positions.
4. **AC-27 report redaction** — `write_report` and `write_comparison` redact JSON and Markdown before persistence; `tests/security/test_report_redaction.py` covers it.
5. **AC-23 examples unit-tested** — 31 plugin tests, including adversarial fixtures; examples import and validate.
6. **AC-28..30 (plus the health endpoint AC-35 depends on)** — `create_app()` constructs, 14 API tests pass, OpenAPI is tested; `/api/v1/health` returns 200 in LIGHT.
7. **AC-31..33 dashboard** — `dist/` exists, 10 routes/12 pages, SPA served by the API, external browser + Lighthouse pass reported.
8. **AC-34 clean install** — wheel contains both packages and both console scripts; light-profile `doctor` and `serve` verified (reported clean-venv install not re-run here).
9. ~~Security audit absent~~ — **resolved**: the file exists and is APPROVED (0 CRITICAL/0 HIGH), with residual risks documented.
10. **TASKS progress counter** — now consistent at 9/14 (TASK-009 verified, TASK-012 in progress).
11. **Attribution vs. packaging** — README rewritten for the fork, `CHANGELOG`/`SECURITY`/`CONTRIBUTING` added, headers preserved; runtime banner still references heretic-project.org by design (upstream identity), documented rather than "fixed".
12. **Quality gate** — closed at 11:20Z: `ruff format --check src tests` → 144 files formatted; `ruff check --extend-select I src tests` → "All checks passed!" (a per-file `E402` ignore for `src/heretic/main.py` documents the intentional upstream import order, and the shim call sites are sorted). `ty check src/fsociety` → 1 warning, no errors.
13. **`pydantic` transitive-only** — closed: `pyproject.toml` now declares `pydantic>=2.9,<3`.
14. **AC-36 final report** — closed: `docs/reports/FINAL_PROJECT_REPORT.md` exists and its limitation statements were cross-checked.

### Remaining (honest)

15. **AC-19's wording implies a stronger guarantee than the evidence.** Concurrency is now covered by a multi-thread store test, but there is still no crash-consistency (kill mid-write) test for the single-connection store.
16. **ADR-003 claims CI runs a light profile and an engine profile** — now true (CI has `platform`, `quality`, `engine`, `frontend`, `upstream-model-tests` jobs), but the light job installs from `deployment/docker/requirements-platform.txt` with `--no-deps -e .`, a different path than the documented `pip install ".[api]"`; divergence risk remains.
17. **AC-35 cannot be closed on this host.** Dockerfile/compose exist; no daemon → image build and container health check are unverified, reported as skipped.
18. **`src/heretic` is no longer byte-identical to upstream** (ADR-011). The SPEC's "untouched" phrasing must be read as "no behavioural change beyond the documented alias shim"; BC-007 encodes that.
19. ~~Security approval is the only delivery blocker~~ — **resolved**: `.factory/SECURITY_AUDIT.md` is APPROVED (0 CRITICAL/0 HIGH). Remaining delivery gaps are environmental: Docker image build and GPU execution unverified.
20. **The CI workflow has not been observed green on GitHub Actions.** Local equivalents pass; the report states this explicitly. Docker and frontend jobs remain unexecuted in this environment.

---

# 9. Verification commands (canonical)

```bash
# LIGHT profile (no torch/transformers/optuna/lm-eval) — measured: 473 passed, 6 skipped
PYTHONPATH=src /tmp/opencode/fsociety-venv/bin/python -m pytest tests -q -m "not gpu and not engine"

# ENGINE profile — measured: 500 passed, 3 skipped
/tmp/opencode/engine-env/bin/python -m pytest tests -q -m "not gpu"

# Upstream engine suite — measured: 47 tests OK
/tmp/opencode/engine-env/bin/python -m unittest discover -s tests -p 'test_*.py'

# Engine CLI under the renamed distribution — measured: rc 0
/tmp/opencode/engine-env/bin/heretic --help

# Build artifacts — measured: wheel 319 KB, 22 heretic/ + 81 fsociety/ entries, 2 console scripts
uv build --out-dir /tmp/opencode/dist-check

# API + SPA without torch — measured: health 200, version 200, / 200
FSOCIETY_WORKSPACE=/tmp/opencode/fsociety-serve \
  PYTHONPATH=src /tmp/opencode/fsociety-venv/bin/python -m fsociety.interfaces.cli.main serve

# Quality gates — measured GREEN at 11:20Z
/tmp/opencode/fsociety-venv/bin/ruff format --check src tests
/tmp/opencode/fsociety-venv/bin/ruff check --extend-select I src tests
/tmp/opencode/fsociety-venv/bin/ty check src/fsociety --ignore import-not-found

# UI (requires Node 22; NOT available on this host)
cd frontend && npm ci && npm run typecheck && npm run build

# Docker (requires a daemon; NOT available on this host)
docker build -f deployment/docker/Dockerfile .
```

---

# 10. Verification evidence appendix (2026-11-23)

| # | Check | Command | Result |
| :--- | :--- | :--- | :--- |
| E-01 | LIGHT suite | `PYTHONPATH=src /tmp/opencode/fsociety-venv/bin/python -m pytest tests -q -m "not gpu and not engine"` | **473 passed, 6 skipped**, 3 subtests passed, 40.83s; skips = 5 upstream torch modules + 1 engine-importable guard |
| E-02 | ENGINE suite | `/tmp/opencode/engine-env/bin/python -m pytest tests -q -m "not gpu"` | **500 passed, 3 skipped**, 5 subtests passed, 57.66s; skips = no-engine failure path, unavailable-torch path, engine-importable guard |
| E-03 | Upstream unittest | `/tmp/opencode/engine-env/bin/python -m unittest discover -s tests -p 'test_*.py'` | **Ran 47 tests — OK** (0.293s) |
| E-04 | Collection | `pytest tests --collect-only -q` (LIGHT) | 472 tests collected; **30 test modules** (unit, api, security, integration + 6 upstream) |
| E-05 | Engine CLI | `/tmp/opencode/engine-env/bin/heretic --help` | usage banner printed, **rc 0**; installed metadata = `fsociety-ai` only |
| E-06 | CLI `--json` | `fsociety <cmd> --json` for version/hardware/models list/security policy/security findings/evaluate suites/plugins list/config show/experiments list | all **rc 0** with JSON; `engine status --json` rc 1 with structured error (engine absent) |
| E-07 | CLI `--json` (legacy position) | `fsociety --json hardware` | rc 0, same payload (both positions accepted) |
| E-08 | Wheel build | `uv build --out-dir /tmp/opencode/dist-check` | wheel + sdist built; **22** `heretic/` entries, **81** `fsociety/` entries, **8** benchmark JSONs, `entry_points.txt` = `fsociety`, `heretic` |
| E-09 | Serve without torch | `serve` on 127.0.0.1:8127, then curl | `/api/v1/health` **200** (`database ok`, `engine unavailable`), `/api/v1/version` **200** (`engine_available=false`), `/` **200** serving `dist/index.html` (696 B), `/api/v1/models` **200** loopback |
| E-10 | Security headers | `curl -sI /api/v1/health` | CSP (ADR-012 variant), nosniff, DENY, no-referrer, permissions policy, COOP, CORP present |
| E-11 | Secret scan | 293 text files, 6 token regexes | **2 matches**, both synthetic fixtures in `tests/unit/test_foundation.py`; **0 in `src/`** |
| E-12 | Placeholder scan | `grep -rIn 'TODO\|FIXME\|XXX\|NotImplementedError' src/fsociety` | **0** |
| E-12b | pydantic declaration | `grep '"pydantic' pyproject.toml` | `pydantic>=2.9,<3` declared explicitly (closes the initial P3-1) |
| E-13 | Lint (format) | `ruff format --check src tests` | **GREEN** — 144 files already formatted |
| E-14 | Lint (rules) | `ruff check --extend-select I src tests` | **GREEN** — "All checks passed!" (per-file `E402` ignore documents upstream import order in `src/heretic/main.py`) |
| E-15 | Types | `ty check src/fsociety --ignore import-not-found` | 1 warning (redundant condition in `core/hardware.py:121`), 0 errors; CI does not run `--error-on-warning` |
| E-16 | Frontend build | not runnable (`node`/`npm` absent) | `frontend/dist` exists (2.7 MB), 12 pages, 10 nav routes; `typescript`/`vite` build, browser E2E and Lighthouse a11y 1.0 reported in `FINAL_PROJECT_REPORT.md` §5.5 |
| E-17 | Docker | not runnable (no daemon) | `deployment/docker/{Dockerfile,docker-compose.yml}` present; **image build unverified** |
| E-18 | Security audit | `.factory/SECURITY_AUDIT.md` | **APPROVED** — 0 CRITICAL / 0 HIGH; 1 accepted MEDIUM (sqlitedict) + 3 accepted LOW |
| E-19 | OpenAPI + docs routes | `curl /api/openapi.json`, `/api/docs` on a live server | **200** both; schema is OpenAPI 3.1.0 titled "FSOCIETY AI API"; docs guide paths match the app (`docs_url=/api/docs`, `openapi_url=/api/openapi.json`) |
| E-20 | API route count | `grep -c '@router.(get\|post\|delete\|put)' routes/*.py` | **35** handlers (matches documentation) |
