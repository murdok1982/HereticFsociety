<div align="center">

# FSOCIETY AI

### Advanced Model Engineering Platform

**Inspect hardware · plan resources · manage models · optimize configurations · evaluate behavior · track experiments**

Built on the preserved [Heretic](https://github.com/p-e-w/heretic) modification engine.

</div>

---

FSOCIETY AI is a local-first platform for engineers who modify, evaluate and ship
language models. It does not replace Heretic: it wraps the existing engine with the
operational layer a real workflow needs — hardware discovery with provenance, memory
planning, model inventory and integrity checks, seeded optimization, reproducible
evaluation, experiment history, isolated plugins, a typed HTTP API and a dashboard.

> **Honesty rules.** This project does not fabricate measurements. Plans are labelled as
> analytical estimates. Bundled evaluation suites are small original smoke suites, not
> MMLU/GSM8K/HumanEval. GPU-only tests are reported as skipped when the hardware is absent.

---

## What is preserved from Heretic

The complete `heretic` package is untouched:

* Arbitrary-Rank Ablation (ARA) and directional ablation (abliteration)
* Refusal suppression, slop/humor/sycophancy modification, response-length steering
* Scorer and modifier plugin contracts (`src/heretic/scorer.py`, `modifier.py`)
* Heretic 2.x configuration files (`config.*.toml`)
* Batch-size benchmarking, dtype fallback, 4-bit loading, export and upload paths
* Reproducibility manifests and the Heretic Grimoire workflow
* The `heretic` command and `import heretic`

FSOCIETY AI adds the `fsociety` package and command alongside it.

---

## Install

Requirements: Python 3.10+ for the platform/API profile (validated on 3.10, 3.12 and 3.14).
The **engine profile requires Python 3.11+**: security floors for Pillow/filelock/dnspython
cannot be satisfied on 3.10, so engine dependencies are installed only on 3.11+. Model
modification and real model evaluation therefore need 3.11 or newer; on 3.10 the CLI reports
the engine as unavailable with install guidance. The engine profile was validated on 3.12
(CPU); GPU acceleration depends on the host.

```bash
# Recommended: reproducible environment
uv venv
uv pip install -e ".[api,dev]"        # source checkout, includes the engine stack

# Or a lightweight deployment (API/dashboard/inventory only, no torch)
uv pip install --no-deps -e .
uv pip install -r deployment/docker/requirements-platform.txt
```

Installing `fsociety-ai` also installs the original Heretic engine dependencies, so
existing workflows keep working. The already-published `heretic-llm` distribution is
superseded; see the [migration guide](docs/guides/MIGRATION.md).

---

## Quick start

```bash
fsociety doctor                       # environment, hardware, store, engine, policy
fsociety hardware                     # CPU/RAM/GPU with source and confidence
fsociety models list                  # local models + Hugging Face cache
fsociety models inspect org/model     # metadata and exact safetensors parameter count
fsociety models estimate org/model --batch-size 1 --sequence-length 4096
fsociety optimize run --model org/model --space space.json --trials 20 --seed 42
fsociety evaluate suites
fsociety evaluate run --suite knowledge_core --backend transformers --model org/model
fsociety experiments list
fsociety serve                        # API + dashboard on http://127.0.0.1:8000
```

The original engine remains available exactly as before:

```bash
heretic Qwen/Qwen3-4B-Instruct-2507
# or, through the platform adapter (same engine, explicit trust policy):
fsociety engine run -- Qwen/Qwen3-4B-Instruct-2507
```

---

## Dashboard

The React dashboard ships ten sections, all fed by the real API:

**Overview · Model Manager · Hardware Monitor · Optimizer · Evaluation Center ·
Experiments · Plugin Manager · Export Center · Security · Settings**

```bash
cd frontend && npm install && npm run build   # produces frontend/dist
fsociety serve
```

If the frontend is not built, the API still runs and `/` explains how to build it.
The dashboard never simulates GPU metrics, model status, jobs or benchmark results.
When the API is unreachable it shows an explicit error state with retry.

---

## API

Base path `/api/v1`; interactive docs at `/api/docs` (disable with
`FSOCIETY_API_DOCS_ENABLED=false`).

```bash
curl http://127.0.0.1:8000/api/v1/health
curl http://127.0.0.1:8000/api/v1/hardware
curl -X POST http://127.0.0.1:8000/api/v1/models/scan \
  -H 'Content-Type: application/json' \
  -d '{"roots": [], "include_hf_cache": true}'
```

Key endpoints: system/health, hardware, models (scan/inspect/estimate/verify),
optimization jobs, evaluation jobs, job cancellation, experiments/trials/artifacts,
evaluation suites/comparison, plugins, exports, security policy/findings/audit/SBOM,
redacted settings.

**Authentication.** The API binds to `127.0.0.1` by default and trusts loopback. Any
non-loopback request fails closed unless `FSOCIETY_API_KEY` is set and supplied via
`X-API-Key` or `Authorization: Bearer`. CORS is disabled unless explicit origins are
configured; wildcard origins are rejected at startup.

---

## Reproducible evaluation

Every report records model identity, backend, suite hash, grader version, seed,
generation configuration, hardware fingerprint, software versions, timings, peak RSS and
per-item results. Comparisons refuse to claim comparability when the suite, grader
version or evaluation configuration differ; hardware differences are surfaced as
warnings. Reports are written as JSON plus Markdown under the workspace.

Bundled suites (`knowledge_core`, `math_word_problems`, `python_coding`,
`instruction_following`, `spanish_language`, `defensive_cybersecurity`,
`hallucination_resistance`) are original, small and transparently documented. Official
benchmarks are not bundled; an optional `lm-evaluation-harness` adapter refuses to run
when the harness is absent instead of inventing numbers.

---

## Security model

* `trust_remote_code` defaults to **never**; an exact `org/name` allowlist is required.
  The engine's interactive confirmation flow is preserved for direct `heretic` use.
* Plugin execution is isolated in a subprocess with an environment allowlist, timeouts,
  output caps, process-group kill and optional network namespace. This is process
  isolation, **not** a container sandbox.
* All filesystem input is jailed with symlink-aware path validation; report and export
  paths are constrained to their workspace roots.
* Logs, API errors and reports pass through secret redaction.
* HTTP: API key, rate limiting, trusted hosts, body limits, CSP and hardening headers.
* Supply chain: CycloneDX SBOM generation and optional `pip-audit` execution.

Report vulnerabilities per [SECURITY.md](SECURITY.md).

---

## Configuration

Precedence: defaults → TOML → `FSOCIETY_*` environment variables.

```bash
fsociety config init --path fsociety.toml
fsociety config show
```

Common variables: `FSOCIETY_WORKSPACE`, `FSOCIETY_API_HOST`, `FSOCIETY_API_PORT`,
`FSOCIETY_API_KEY`, `FSOCIETY_TRUST_REMOTE_CODE` (`never`/`allowlist`/`always`),
`FSOCIETY_TRUSTED_REPOSITORIES`, `FSOCIETY_PLUGIN_ISOLATION`,
`FSOCIETY_PLUGIN_ALLOW_NETWORK`, `FSOCIETY_EVALUATION_SEED`, `FSOCIETY_OFFLINE`.

The engine keeps its own configuration contract (`config.toml` or
`HERETIC_CONFIG_FILE`); the platform never rewrites your Heretic configs.

---

## Workspace layout

```
~/.local/share/fsociety/     # or $FSOCIETY_WORKSPACE
├── data/fsociety.sqlite3    # experiments, trials, jobs, events, artifacts
├── models/                  # local model directories
├── reports/                 # evaluation JSON + Markdown
├── exports/                 # verifiable export bundles
├── audits/                  # SBOM + security reports
├── plugins/                 # workspace plugins
├── logs/audit.jsonl         # redacted audit trail
└── cache/
```

---

## Development

```bash
uv sync --extra api --extra dev
uv run ruff check src/fsociety tests
uv run ruff format --check src/fsociety tests
uv run pytest -q
cd frontend && npm install && npm run typecheck && npm run build
```

The platform core is tested without torch. Engine and GPU tests are marked and skipped
when unavailable; skips are reported explicitly. See
[CONTRIBUTING.md](CONTRIBUTING.md) and the SDD set under `docs/sdd/`.

---

## Documentation

| Document | Content |
| :--- | :--- |
| [Discovery](docs/sdd/00_DISCOVERY.md) | Repository map and baseline |
| [Requirements](docs/sdd/01_REQUIREMENTS.md) | FR/NFR/SEC with acceptance criteria |
| [Architecture](docs/sdd/02_ARCHITECTURE.md) | Components, contracts, security boundaries |
| [Implementation plan](docs/sdd/03_IMPLEMENTATION_PLAN.md) | Atomic task graph |
| [Decisions](docs/sdd/DECISIONS.md) | ADRs and trade-offs |
| [Initial audit](docs/audit/INITIAL_AUDIT.md) | Findings and risks |
| [Migration](docs/guides/MIGRATION.md) | From `heretic-llm` to `fsociety-ai` |
| [Security](SECURITY.md) | Threat model and reporting |
| [Final report](docs/reports/FINAL_PROJECT_REPORT.md) | Verification evidence |

---

## License and attribution

Copyright © 2025–2026 Philipp Emanuel Weidmann and Heretic contributors.  
FSOCIETY AI platform additions © 2025–2026 FSOCIETY AI contributors.

This program is free software under the **GNU Affero General Public License v3.0 or
later**. It is distributed in the hope that it will be useful, but **without any
warranty**; without even the implied warranty of merchantability or fitness for a
particular purpose. See [LICENSE](LICENSE) for the full text.

Under AGPL-3.0 section 13, users interacting with this software over a network may obtain
the Corresponding Source at <https://github.com/murdok1982/HereticFsociety>. The API also
returns this location as `source_url` from `/api/v1/version`.

The original Heretic project, its algorithms, documentation, branding and license
notices are preserved. If you use the engine in research, cite the original work:

```bibtex
@misc{heretic,
  author = {Weidmann, Philipp Emanuel},
  title = {Heretic: Training-free language model modification},
  year = {2025},
  publisher = {GitHub},
  howpublished = {\url{https://github.com/p-e-w/heretic}}
}
```
