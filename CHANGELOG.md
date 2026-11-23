# Changelog

All notable changes to FSOCIETY AI. The engine history prior to 3.0.0 is the upstream
Heretic changelog (https://github.com/p-e-w/heretic/releases).

## [3.0.0] — 2026-11-23

### Added — platform layer (`fsociety`)

- **Hardware detection** with per-field provenance and confidence: CPU, RAM, NVIDIA
  (`nvidia-smi`), AMD (`rocm-smi`), torch CUDA/ROCm/MPS probes, stable hardware
  fingerprints. Never infers accelerator support from the OS.
- **Resource estimator**: analytical weights/KV-cache/activations/overhead model with
  documented assumptions, fit decisions and actionable dtype/quantization/batch
  alternatives. Labelled as estimates everywhere.
- **Model manager**: torch-free inspection of local directories and the Hugging Face
  cache, exact parameter counts from safetensors headers, shard support, MoE/multimodal
  detection, revisions/commits, SHA-256 manifests and verification, guarded downloads.
- **Optimizer**: declarative search spaces mapped to seeded Optuna TPE/random sampling,
  full trial persistence, cooperative cancellation, failure isolation, hardware-aware
  planning and a shared analytical resource-planning objective.
- **Evaluation framework**: seven original smoke suites, deterministic versioned graders,
  Transformers/llama.cpp backends (lazy, seeded), provenance-complete JSON+Markdown
  reports, per-category metrics, regression comparison with explicit comparability rules,
  optional `lm-evaluation-harness` adapter.
- **Experiments**: SQLite (WAL) store for experiments, trials, jobs, events and artifacts,
  JSON export, hardware fingerprint and config/suite hashes.
- **Plugins**: versioned platform plugin API, discovery (builtins, entry points, workspace),
  permission/dependency validation, subprocess isolation with timeouts, output caps,
  process-group kill and optional network namespace; seven example scorers.
- **Exporters**: verifiable bundles with manifests and license/metadata; honest GGUF
  boundary that executes llama.cpp tools only when present.
- **Security**: `trust_remote_code` policy (never by default, exact allowlist), symlink-aware
  path jail, secret redaction in logs/errors/reports, API key auth with constant-time
  comparison, trusted hosts, rate limiting, body limits, CORS allowlist, security headers,
  configuration findings, CycloneDX SBOM, dependency audit wrapper.
- **HTTP API** (`/api/v1`): system/hardware, models, optimization/evaluation jobs with
  cancellation and progress, experiments/trials/artifacts/reports, suites/comparison,
  plugins, exports, security policy/findings/audit/SBOM, redacted settings. Structured
  errors, request IDs, OpenAPI docs, optional SPA serving.
- **CLI** (`fsociety`): doctor, hardware, models (list/scan/inspect/verify/estimate/download),
  optimize, evaluate (suites/run/report/compare), experiments, plugins, security
  (policy/findings/audit/sbom/deps), export, serve, config, engine adapter.
- **Dashboard** (`frontend/`): React 19 + TypeScript + Vite, dark technical design system,
  ten sections, real API data, loading/empty/error states, job polling, responsive layouts,
  accessible controls, API key session handling.
- **Delivery**: SDD documentation, initial audit, ADRs, migration guide, security policy,
  CI matrix, Docker CPU deployment, install scripts, final verification report.

### Changed

- Distribution renamed `heretic-llm` → `fsociety-ai`; version `3.0.0`.
- Build backend `uv_build` → `hatchling` to ship `heretic` and `fsociety`.
- `heretic` console script retained; `fsociety` added.

### Preserved

- The entire Heretic engine: ARA/abliteration, scorers, modifiers, configuration contract,
  reproducibility manifests, export/upload flows, license and attribution.

### Known limitations

- Plugin isolation is process-level, not a container sandbox.
- Rate limiting/job execution are per-process.
- Bundled suites are smoke suites, not leaderboard benchmarks.
- GPU/engine validation is unavailable on the CPU-only validation host; those paths are
  reported as skipped/unverified.
