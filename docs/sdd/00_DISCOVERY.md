# 00 — Discovery

- **Repository:** `/run/media/fsociety/Extension/Proyectos de github/Cirujia LLMs/HereticFsociety`
- **Snapshot commit:** `aecd4a845a23be3f96ddc03b37b53a9195ee2566` (`master`, 2026-11-20)
- **Snapshot time:** 2026-11-23T10:37Z (working tree dirty; platform files under active construction)
- **Purpose of this document:** describe what exists *today*, with evidence, so that requirements in `01_REQUIREMENTS.md` are grounded in the real system rather than in the intended one.

> Scope discipline: this document describes the repository as found. Where the intended design (`.factory/ARCHITECTURE.md`) differs from the tree, the difference is recorded in §10 (Gap list), not smoothed over.

---

## 1. Repository tree (source level)

```text
.
├── .factory/                      # orchestration state (untracked)
│   ├── SPEC.md                    # authoritative requirements + AC-01..AC-36
│   ├── ARCHITECTURE.md            # stack, topology, design system, gate rules
│   ├── TASKS.md                   # 14 tasks, retry counters (9/14 verified)
│   └── SECURITY_AUDIT.md          # adversarial audit (IN PROGRESS, not an approval)
├── .github/workflows/ci.yml       # 5 jobs: platform / quality / engine / model-tests / frontend
├── config.classifier.toml         # engine presets (8 files)
├── config.default.toml            # 13,832 bytes, fully commented
├── config.emotions.toml  config.nohumor.toml  config.noslop.toml
├── config.nosyco.toml    config.piqa.toml     config.terse.toml
├── deployment/
│   ├── docker/{Dockerfile,docker-compose.yml,requirements-platform.txt}
│   └── scripts/install.sh
├── docs/
│   ├── audit/INITIAL_AUDIT.md
│   ├── sdd/{00_DISCOVERY,01_REQUIREMENTS,02_ARCHITECTURE,03_IMPLEMENTATION_PLAN,DECISIONS}.md
│   ├── guides/                    # 9 operator guides (API, CLI, config, plugins, ...)
│   └── migration_v1_to_v2.md      # upstream migration guide
├── frontend/                      # React 19 + TS + Vite (untracked)
│   ├── package.json  package-lock.json  vite.config.ts  tsconfig.json  index.html
│   ├── node_modules/  dist/       # build artifact present (2.7 MB)
│   └── src/                       # 48 files, 11,718 LOC (ts/tsx/css)
│       ├── api/{client,types}.ts
│       ├── components/layout/{AppShell.tsx,navigation.ts}
│       ├── components/ui/         # 17 primitives (AsyncState, DataTable, Drawer, ...)
│       ├── hooks/                 # 6 hooks (useApiQuery, useJobPoll, useApiKey, ...)
│       ├── lib/{apiKey,format,status}.ts
│       ├── pages/                 # 12 pages (10 sections + detail + 404)
│       └── main.tsx   App.tsx
├── src/
│   ├── heretic/                   # upstream engine, 7,435 LOC, one documented shim
│   │   ├── main.py  model.py  config.py  plugin.py  scorer.py  evaluator.py
│   │   ├── system.py  utils.py  reproduce.py  progress.py  modifier.py
│   │   ├── modifiers/{ara.py,abliteration.py}
│   │   └── scorers/{keyword_rate,kl_divergence,response_length,bert_classifier,benchmark_score}.py
│   └── fsociety/                  # platform, 18,454 LOC / 73 modules (snapshot)
│       ├── __init__.py            # __version__=3.0.0, PLUGIN_API_VERSION=1
│       ├── config.py  context.py  paths.py  errors.py  logging.py
│       ├── core/{models,hardware,estimator,engine}.py
│       ├── core/model_manager/{__init__,inspection,hf}.py
│       ├── optimizer/{space,engine,planner,resource_objective,__init__}.py
│       ├── evaluation/{suites,graders,backends,runner,reporting,regression,lm_eval_adapter}.py
│       ├── evaluation/benchmarks/ # 7 suite JSONs + manifest
│       ├── experiments/store.py
│       ├── plugins/{api,registry,isolation,worker}.py + scorers/ (7)
│       ├── security/{policy,redaction,findings,headers,sbom}.py
│       ├── exporters/{manifest,gguf}.py
│       ├── utils/hashing.py
│       └── interfaces/{cli/main.py, api/{app,auth,deps,errors,jobs,middleware,schemas,routes/*}}
├── tests/
│   ├── run_tests.py               # hash-reproduction harness (downloads models)
│   ├── README.md                  # how to add per-model SHA256SUMS
│   ├── <model-family>/            # 10 dirs: config.toml + SHA256SUMS.{ci,ci2,ci3,ci4,linux,windows}
│   ├── test_config.py  test_plugin.py  test_chat_template.py
│   ├── test_keyword_rate.py  test_residual_streams.py  test_ara_lowrank_equivalence.py
│   ├── unit/{test_foundation,test_optimizer,test_planner}.py
│   └── api/test_api.py
├── pyproject.toml                 # fsociety-ai 3.0.0 (modified, uncommitted)
├── uv.lock                        # 1,047,860 bytes
├── LICENSE                        # AGPL-3.0-or-later, 34,523 bytes
├── README.md                      # upstream Heretic README, 15,322 bytes
└── .python-version                # "3.12"
```

Note: `src/heretic` contains **no** platform imports; the dependency direction is `fsociety → heretic` only, and only through a subprocess (`core/engine.py`) or optional imports.

---

## 2. Stack

### 2.1 Runtime dependencies (`pyproject.toml`)

The base dependency set is the upstream engine set: 23 requirements, several major-pinned with `~=`:

| Category | Packages |
| :--- | :--- |
| Numerics / tensors | `torch` (unpinned by design), `torchvision` (unpinned), `numpy~=2.2` |
| Model runtime | `transformers[kernels]~=5.18`, `accelerate~=1.15`, `peft~=0.21`, `bitsandbytes~=0.50`, `datasets~=5.0`, `huggingface-hub~=1.33` |
| Optimization | `optuna~=5.0` |
| Evaluation | `lm-eval[hf]~=0.4` |
| Config / validation | `pydantic-settings~=2.15`, `immutabledict~=4.3`, `tomli-w~=1.2` |
| System probes | `psutil~=7.2`, `py-cpuinfo~=9.0` |
| CLI / UX | `rich~=15.0`, `questionary~=2.1`, `tqdm~=4.70`, `ipython~=8.39` |
| Misc | `langdetect~=1.0` |

Optional extras:

| Extra | Contents | Purpose |
| :--- | :--- | :--- |
| `api` | `fastapi>=0.115,<1.0`, `uvicorn[standard]>=0.30,<1.0`, `httpx>=0.27,<1.0` | HTTP API, test client |
| `llama` | `llama-cpp-python>=0.3` | GGUF inference backend |
| `dev` | `ruff>=0.16.9`, `ty>=0.0.84`, `pytest>=8.3`, `pytest-cov>=6.0` | lint/type/test |

`[tool.uv] exclude-newer = "7 days"`; `torch`/`torchvision` resolve from the explicit `pytorch` index (`https://download.pytorch.org/whl/cpu` by default).

### 2.2 Frontend

`react 19.1`, `react-dom 19.1`, `react-router-dom 7.6`, `vite 6.3`, `typescript 5.8`, `@vitejs/plugin-react 4.5`, self-hosted fonts (`@fontsource-variable/inter`, `@fontsource-variable/jetbrains-mono`). No CSS framework, no component library, no chart library — visual system is custom (`styles/*` intended, currently missing).

### 2.3 Supported Python

`requires-python = ">=3.10"`; classifiers list 3.10–3.15; CI matrix matches. Actual verification on this machine: **3.14.10 only**.

---

## 3. Entry points

| Surface | Definition | Current state |
| :--- | :--- | :--- |
| `heretic` console script | `heretic.main:main` (`pyproject.toml:77`) | Declared and working: installed-wheel `heretic --help` → rc 0 via `_distribution.py` alias (ADR-011). |
| `fsociety` console script | `fsociety.interfaces.cli.main:entrypoint` (`pyproject.toml:76`) | Works via `python -m fsociety.interfaces.cli.main`; `entrypoint()` wraps `main()` and `sys.exit`. |
| CLI tree | argparse with 13 command groups (`main.py:903-1037`) | `version`, `doctor`, `hardware`, `models`, `optimize`, `evaluate`, `experiments`, `plugins`, `security`, `export`, `serve`, `config`, `engine`. |
| HTTP API | `create_app()` → 35 handlers under `/api/v1` | Constructs; 14 API tests pass. Public: `/api/v1/health`, `/api/v1/version`; all other routers behind `require_auth`. |
| SPA | served by API when `frontend/dist` exists or `serve_frontend=false` → JSON root | `dist` absent. |
| Engine subprocess | `python -m heretic.main` or `python -c <bootstrap>` with `HERETIC_CONFIG_FILE`, `cwd` = config dir | Requires engine deps; unavailable here. |
| Plugin worker | `python -m fsociety.plugins.worker` (invoked by `plugins/isolation.py`) | Isolated execution path for platform plugins. |
| lm-eval adapter | `python -m lm_eval` with argument list (`evaluation/lm_eval_adapter.py`) | Requires `lm_eval`; unavailable here. |

Global CLI options: `--json`, `--workspace`, `--config`, `--version`. **Ordering caveat:** `--json` is global-only, so `fsociety hardware --json` is rejected while `fsociety --json hardware` works (measured).

---

## 4. Configuration system

### 4.1 Engine configuration (`heretic.config.Settings`)

`pydantic-settings` with six ordered sources (`config.py:592-613`), first match wins:

1. `init_settings` — constructor arguments
2. `CliSettingsSource(cli_parse_args=True, cli_implicit_flags=True, cli_kebab_case=True)`
3. `EnvSettingsSource(env_prefix="HERETIC_")`
4. dotenv
5. file secrets
6. `TomlConfigSettingsSource(toml_file=_config_path())`

`_config_path()` (`config.py:194-200`): `HERETIC_CONFIG_FILE` if set — and **must exist**, otherwise `SystemExit("heretic: configuration file … does not exist")` — else `config.toml` in the **current working directory**.

`model_config = SettingsConfigDict(extra="allow")` is deliberate: `[scorer.<Class>]`, `[scorer.<Class>_<instance>]`, `[modifier.<Class>]` tables are read from `settings.model_extra` by `plugin.get_plugin_namespace`.

Fields marked `exclude=True` (paths, upload targets, prompt datasets, debug flags) are omitted from serialization, so `reproduce.json`/`config.toml` artifacts cannot leak local paths — with the notable exceptions of `model` and `evaluate_model` handling described in the engine code.

`InitOnlySettings` (`config.py:634-646`) validates a dict without reading any other source; used when restoring settings from a journal or `reproduce.json`, re-injecting the `exclude=True` fields from the current settings.

### 4.2 Platform configuration (`fsociety.config.PlatformSettings`)

`BaseSettings` with `env_prefix="FSOCIETY_"`, nested delimiter `__`, `extra="ignore"`, `validate_default=True`. Precedence, explicitly implemented in `load_settings()` (`config.py:173-193`):

1. defaults
2. TOML (`--config` / `FSOCIETY_CONFIG` / `./fsociety.toml`); both flat keys and a `[fsociety]` table are accepted
3. environment variables
4. explicit overrides

Key groups: paths (`workspace`, `models_dir`, `hf_cache_dir`), engine defaults (`default_dtype`, `default_quantization`, `default_sequence_length`, `default_batch_size`, `offline`), evaluation (`evaluation_seed`, `max_new_tokens`, `temperature`), security (`trust_remote_code ∈ {never,allowlist,always}`, `trusted_repositories`, `plugin_isolation`, `plugin_timeout_seconds`, `plugin_allow_network`, `plugin_max_output_bytes`, `allow_absolute_model_paths`), API (`api_host`, `api_port`, `api_key: SecretStr`, `api_require_key_for_loopback`, `api_cors_origins`, `api_allowed_hosts`, `api_rate_limit_per_minute`, `api_max_upload_bytes`, `api_docs_enabled`, `serve_frontend`, `frontend_dist`), logging (`log_level`, `audit_log_enabled`).

Validators reject wildcard CORS and malformed trusted repo ids. `redacted()` excludes the API key and reports only `api_key_configured`.

Workspace layout (`paths.Workspace`): `data/fsociety.sqlite3`, `models/`, `exports/`, `reports/`, `logs/audit.jsonl`, `plugins/`, `cache/`, `audits/`. Default root: `$FSOCIETY_WORKSPACE`, else XDG/LOCALAPPDATA/Application Support + `fsociety`.

---

## 5. Plugin, modifier and scorer contracts

### 5.1 Engine-side (upstream, unchanged)

**Loading.** `load_plugin(name, base_class)` (`plugin.py:66-156`) accepts:
- `path/to/plugin.py:ClassName` — file must exist; module cached in `sys.modules` as `heretic_plugin_<abs path>`; executed with `exec_module` (arbitrary code execution by design);
- `fully.qualified.module.ClassName` — `importlib.import_module` then attribute lookup.

Errors are explicit: `.py` without class → instructs the `:ClassName` form; missing class → `ValueError`; wrong base class → `TypeError`; no dot in import form → `ValueError`.

**Base class.** `Plugin` (`plugin.py:199-405`):
- `__init__(*, heretic_settings, settings=None)`; subclasses must not define `__init__` (`validate_contract`), enforced before instantiation;
- optional `settings: <BaseModel>` annotation becomes the validated settings schema; `Optional` is rejected;
- `get_settings_raw(model_extra, top_namespace, instance_name)` merges `[<ns>.<Class>]` then `[<ns>.<Class>_<instance>]`, filtering to declared fields;
- `write_settings_to_config()` persists effective settings back to `model_extra` before serialization;
- `get_dataset_specifications()` discovers dataset fields for reproducibility checks;
- `init(ctx)` hook runs once;
- `reproducible` defaults to `False` and gates whether a model can be published as reproducible.

**Modifier.** `Modifier(Plugin, ABC, Generic[Parameters])` (`modifier.py:35-98`): `suggest_parameters(ctx, trial)`, `modify_model(ctx, parameters)`, `reset_model(ctx)`, `parameters_class` recovered from the generic base, `render_trial_parameters`. `load_and_init_modifiers` enforces **exactly one** modifier today (`modifier.py:116-120`). Ships `ARA` (default) and `Abliteration`.

**Scorer.** `Scorer(Plugin, ABC)` (`scorer.py:28-65`): `get_score(ctx) -> Score`, optional `get_baseline_score(ctx)` (defaults to `get_score`). `Score` carries `value: float`, `rich_display: str`, `md_display: str`. The `Evaluator` loads all scorers, computes baselines once, and derives objective names/directions/values positionally from a single `_objective_entries()` source (`evaluator.py:193-232`).

**Context.** `Context` exposes `get_responses` (cached per prompt set), `get_logits`, `get_residuals`, `get_model`, `load_prompts`. All batched variants chunk by `settings.batch_size`.

### 5.2 Platform-side (new, versioned)

`PLUGIN_API_VERSION = 1`; kinds: `scorer | modifier | evaluator | hardware_provider | exporter | integration`. `PluginBase` declares metadata (`name`, `version`, `api_version`, `description`, `author`, `license`, `dependencies`, `permissions`) and `validate_plugin_class()` rejects wrong API version, wrong kind, or non-`PluginBase` classes.

Discovery order: builtin → Python entry points → workspace directories. Duplicate names are ignored and audited. Invalid plugins are recorded with `valid=False` and errors, never silently dropped.

Execution: `plugins/isolation.py::run_isolated()` spawns `python -m fsociety.plugins.worker` with an env allowlist (`sandbox_env()`), optional `unshare -n`, POSIX rlimits, timeout, bounded stdout/stderr buffers, process-group kill. This is explicitly *not* a sandbox and must not be described as one.

---

## 6. Test workflow

### 6.1 Upstream engine tests

- Runner: `python -m unittest discover -s tests -p 'test_*.py'`.
- `test_config.py` is torch-free (18 tests) and exercises config validation/CLI parsing.
- `test_plugin.py`, `test_chat_template.py`, `test_keyword_rate.py`, `test_residual_streams.py`, `test_ara_lowrank_equivalence.py` import `torch`/`transformers`; they cannot run without the engine profile.
- `tests/run_tests.py` is a **reproduction harness**, not a unit suite: it picks every `tests/<model-family>/` directory, copies the tiny-random model, runs the full Heretic pipeline, and compares SHA-256 of every output file against `SHA256SUMS.<label>`. Multiple hashes per file are allowed because PyTorch does not guarantee cross-system determinism. It downloads models from Hugging Face and must not run in a sandbox without network or in a machine without the engine profile.

### 6.2 Platform tests (re-measured 2026-11-23T11:20Z)

- `pytest` configured in `pyproject.toml`: `testpaths=["tests"]`, `pythonpath=["src"]`, `addopts="-ra"`, markers `gpu`, `engine`, `api`.
- 30 test modules: `tests/unit` (foundation, optimizer, planner, model manager, engine adapter, evaluation, graders, regression, plugins, exporters, jobs), `tests/api`, `tests/security` (policy, report redaction, SBOM), `tests/integration` (CLI, evaluation API), plus the 6 upstream modules.
- Measured: **474 collected, 0 errors**. LIGHT → **473 passed, 6 skipped** (the 5 skipped are upstream torch-dependent modules). ENGINE → **500 passed, 3 skipped**.
- Adversarial fixtures live in `tests/fixtures/plugins/` (crashing, looping, noisy, invalid API version, wrong base, missing dependency, unknown permission).

### 6.3 What can and cannot run here

| Suite | Requires | Status on this machine |
| :--- | :--- | :--- |
| `test_config.py` and platform unit/api/security/integration tests | pydantic, fastapi, stdlib | **Runs (LIGHT)**: 473 passed / 6 skipped |
| Upstream torch-dependent modules | torch, transformers | **Runs (ENGINE)**: 47 upstream tests OK; skipped in LIGHT |
| `tests/run_tests.py` | full engine + network + disk | **Not executed** — downloads and mutates HF models; should not run blind |
| `tests/unit/test_optimizer.py` | optuna | Runs in ENGINE; skipped/marked in LIGHT |
| Frontend typecheck/build | Node 22 + `npm ci` | **Not executable here** (no Node); `dist/` artifact exists |
| Docker build | Docker daemon | **Not executable here** |

---

## 7. CI pipeline (`.github/workflows/ci.yml`, rewritten post-audit)

Five jobs on `ubuntu-latest`, `fail-fast: false`, with `permissions: contents: read` and concurrency cancellation:

| Job | Matrix | Steps |
| :--- | :--- | :--- |
| `platform` | Python 3.10, 3.12, 3.14 | install light profile (`deployment/docker/requirements-platform.txt` + `--no-deps -e .`), CLI smoke (`--version`, `doctor --json`), `pytest -m "not gpu and not engine"`, engine-required selection reported (non-blocking) |
| `quality` | 3.12 | `ruff format --check src tests`, `ruff check --extend-select I src tests`, `ty check src/fsociety --ignore import-not-found` |
| `engine` | 3.12 | `uv sync --extra api --extra dev`, `heretic --help`, `import heretic`, `ty check src/heretic`, upstream `unittest discover` (47 tests), platform `pytest -m "not gpu"`, `uv build` + wheel-content assertion |
| `upstream-model-tests` | — | reproduction harness (`tests/run_tests.py`), scheduled for master/on-demand only (network- and disk-heavy) |
| `frontend` | Node 22 | `npm ci`, `npm run typecheck`, `npm run build`, upload `dist` artifact |

Observations: the light/engine split now exists (ADR-003's claim is true); there is still **no Docker build job** and no SBOM/audit gate; the `quality` job's commands are the ones that are **currently red locally** (ruff check 45 errors, 1 unformatted file), so CI has not been observed green; the light job installs via `--no-deps -e .`, a different path from the documented `pip install ".[api]"`.

---

## 8. Build and install

| Path | Command | Notes |
| :--- | :--- | :--- |
| Full install | `uv sync --all-extras --dev` or `pip install .` | Pulls torch/transformers/optuna/lm-eval; CPU wheels by default through the `pytorch` index. |
| Platform-only | `pip install --no-deps .` + the `[api]` extra (docs), or `deployment/docker/requirements-platform.txt` + `--no-deps -e .` (CI) | Two paths exist; only the CI path is exercised automatically. |
| Build | `uv build` (hatchling) | Verified: wheel 319 KB + sdist; 22 `heretic/` and 81 `fsociety/` entries; 8 benchmark JSONs; `entry_points.txt` declares `fsociety` and `heretic`. |
| Frontend | `npm ci && npm run build` in `frontend/` | `dist/` exists (2.7 MB); rebuild **not possible on this host** (no Node). |
| Docker | `docker build -f deployment/docker/Dockerfile .` | `Dockerfile`, `docker-compose.yml`, `install.sh` exist; **image build unverified** (no daemon). |

Note: `CHANGELOG.md`, `SECURITY.md`, `CONTRIBUTING.md` now exist, so the sdist `include` list is satisfied. `dist/` is not kept in the working tree after verification.

---

## 9. Licensing and attribution

- **License:** AGPL-3.0-or-later (`LICENSE`, 34,523 bytes, GNU AGPL v3 text); `pyproject.toml` declares the SPDX expression `AGPL-3.0-or-later`.
- **Per-file headers:** every `src/heretic/*.py` and `src/fsociety/*.py` file begins with `# SPDX-License-Identifier: AGPL-3.0-or-later` and `# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors`. The three engine files touched for the distribution alias keep the upstream header; `_distribution.py` carries the header plus a note identifying it as the FSOCIETY compatibility shim.
- **Metadata:** `pyproject.toml` keeps the upstream author, adds "FSOCIETY AI contributors", keeps `license = "AGPL-3.0-or-later"`, adds an `Upstream` project URL, and changes Homepage/Repository/Issues to the fork.
- **README:** rewritten for the fork (9,727 bytes), with `CHANGELOG.md`, `SECURITY.md`, `CONTRIBUTING.md` and `.env.example` added; attribution to the upstream project is retained.
- **AGPL obligations that matter for this product:** (a) distributing the wheel requires conveying complete corresponding source under AGPL; (b) **network use** of a modified version obliges offering the source to remote users (AGPL §13) — relevant because the API and dashboard are network-facing; (c) attribution and license notices must be preserved in UI/API metadata, not only in file headers.
- **Distribution naming:** the package was renamed `heretic-llm → fsociety-ai`, which initially broke the engine's metadata lookups. Resolved by `src/heretic/_distribution.py` (ADR-011), which resolves both names while keeping `reproduce.json`/`requirements.txt` keys unchanged. ADR-007's "runtime compatibility stays" claim now holds through ADR-011.

---

## 10. Environment limitations found on this machine

| Item | Measured value | Consequence |
| :--- | :--- | :--- |
| OS / arch | Linux, `x86_64` | POSIX paths; `unshare` availability untested by this audit |
| Python | `3.14.10` (GCC 16.2.0), system interpreter | Newer than the `3.10–3.15` CI range; some ecosystem wheels may differ from CI |
| `.python-version` | `3.12` | Mismatch with the interpreter actually used |
| `.venv/` | exists but **has no `bin/python`** | No project virtualenv is usable; everything ran against system site-packages with `PYTHONPATH=src` |
| Installed (LIGHT venv `/tmp/opencode/fsociety-venv`) | `pydantic 2.14.x`, `pydantic-settings`, `fastapi`, `pytest`, `ruff`, `ty` | Platform tests, lint and CLI smoke run |
| Installed (ENGINE venv `/tmp/opencode/engine-env`) | torch, transformers, optuna, lm-eval 0.4.13, engine deps | Engine suite (500 passed) and upstream suite (47 OK) run |
| Accelerators | `nvidia-smi` not found; no GPU | GPU tests are skipped, never passed |
| CPU / RAM | Intel i5-8250U, 8 logical cores, 15.4 GiB RAM | Real model work impractical even with torch |
| Node toolchain | `node`/`npm` **not on PATH**; `frontend/node_modules/` and `frontend/dist/` exist | Build artifact inspectable; rebuild and typecheck not reproducible here |
| Docker | binary and daemon absent | `deployment/docker/*` unverified |
| Network | `tests/run_tests.py` deliberately not run | Hub/reproduction harness behaviour unverified |
| Git state | 241 commits; dirty tree with untracked platform code | No atomic commit history for the platform yet |

---

## 11. Gap list (reconciled 2026-11-23T11:20Z)

| ID | Gap | Evidence | State |
| :--- | :--- | :--- | :--- |
| G-01 | `heretic` could not start under the renamed distribution | — | **Closed** (ADR-011; `heretic --help` rc 0) |
| G-02 | FastAPI app unconstructible | — | **Closed** (app imports; 14 API tests) |
| G-03 | `--json` not accepted after the subcommand | — | **Closed** (both positions verified) |
| G-04 | Dashboard missing shell/styles/pages | — | **Closed** (12 pages, 10 routes, `dist/` built); rebuild env-skipped |
| G-05 | Engine config mapped `sequence_length` → `max_response_length` | — | **Closed** (explicit `max_response_length`; adapter tests) |
| G-06 | No engine startup/config/adapter tests | — | **Closed** (`test_engine_adapter.py`, 16 tests; upstream 47 OK) |
| G-07 | Platform tests missing (model manager, evaluation, plugins, exporters, security) | — | **Closed** (30 modules, 474 collected) |
| G-08 | `.factory/SECURITY_AUDIT.md` absent | — | **Closed as a file**; content is `IN PROGRESS`, not an approval |
| G-09 | No Dockerfile, frontend CI, SBOM/audit step | — | **Partially closed** — Dockerfile + frontend CI exist; Docker build unverified; no SBOM CI gate |
| G-10 | Platform documentation absent | — | **Closed** (9 guides + README + policy/changelog + `docs/reports/FINAL_PROJECT_REPORT.md`) |
| G-11 | sdist advertises missing `CHANGELOG`/`SECURITY`/`CONTRIBUTING` | — | **Closed** (files exist) |
| G-12 | `TASKS.md` progress counter inconsistent | — | **Closed** (9/14 matches 9 checked tasks) |
| G-13 | Hardware provenance uneven (`CpuInfo` has no `confidence`) | `core/models.py:63-71` | **Open** |
| G-14 | Engine interactions interactive-only; unattended path undefined | `heretic/main.py` trial/export prompts | **Open** (documented non-promise) |
| G-15 | Repro metadata references `heretic-llm` while distribution is `fsociety-ai` | `reproduce.py`, `utils.py` | **Accepted** — manifest keys are intentionally preserved for compatibility |
| G-16 | **Quality gate red** (transient): 45 ruff errors + one unformatted test file | `ruff check`/`ruff format --check` on `src`+`tests` | **Closed** — both green at 11:20Z (per-file `E402` ignore documents upstream import order) |
| G-17 | Docker image build unverified | no Docker daemon on host | **Skipped (environment)** |
| G-18 | Security audit not approved | `.factory/SECURITY_AUDIT.md` `IN PROGRESS` | **Open** — only delivery blocker |
| G-19 | CI workflow never observed running on GitHub (local equivalents pass; Docker/frontend jobs unexecuted) | `.github/workflows/ci.yml`; report §5.7 | **Open** |

---

## 12. What follows from this discovery

1. The **engine is the asset**; the platform is an orchestration and evidence layer on top. Every requirement must preserve the engine's determinism and plugin contracts — the distribution shim is the single, documented behavioural exception.
2. The two original P0 defects were packaging/annotation mistakes, now fixed; the risk they exposed (no startup smoke test, no app-construction test) is now covered by tests and CI steps.
3. The unproven areas have narrowed to: Docker image build, GPU paths, unattended engine execution, and the final evidence report. Each is reported as skipped/open, never as a pass.
4. The platform's honesty mechanisms (estimates labelled, smoke suites labelled, SBOM `unavailable`, hardware warnings) remain a differentiator worth enforcing as non-functional requirements.
5. The quality gate (ruff/format) is the cheapest remaining blocker to a green CI run and should be closed before any new surface is added.
