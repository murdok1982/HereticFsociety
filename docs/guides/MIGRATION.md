# Migrating from `heretic-llm` 2.x / HereticFsociety to FSOCIETY AI 3.0

FSOCIETY AI keeps the Heretic engine and its contracts intact. This guide covers the
packaging change and the new platform layer.

## 1. What changed

| Area | Before | After |
| :--- | :--- | :--- |
| Distribution | `heretic-llm` | `fsociety-ai` |
| Version | `2.2.0.dev0` | `3.0.0` |
| Console scripts | `heretic` | `heretic` **and** `fsociety` |
| Build backend | `uv_build` | `hatchling` (ships `heretic` + `fsociety`) |
| Import paths | `heretic.*` | `heretic.*` (unchanged) + `fsociety.*` (new) |
| Heretic config | `config.toml` / `HERETIC_CONFIG_FILE` | unchanged |
| Heretic plugins | import path or `file.py:Class` | unchanged |
| Platform config | — | `fsociety.toml` / `FSOCIETY_*` (separate) |

## 2. Upgrade procedure

```bash
# 1. Note your environment (for rollback and reproducibility)
python -V
pip freeze > requirements-before.txt

# 2. Remove the old distribution to avoid two packages owning the `heretic` script
pip uninstall -y heretic-llm

# 3. Install FSOCIETY AI (engine included)
pip install -U fsociety-ai[api]

# 4. Verify
fsociety doctor
heretic --help
```

With `uv`:

```bash
uv pip uninstall heretic-llm
uv pip install "fsociety-ai[api]"
uv run fsociety doctor
```

For a lightweight host that only runs inventory/API/dashboard:

```bash
pip install --no-deps fsociety-ai
pip install "fastapi>=0.115,<1.0" "uvicorn[standard]>=0.30,<1.0" \
  "pydantic>=2.9" "pydantic-settings>=2.5" "rich>=13" "psutil>=6" \
  "py-cpuinfo>=9" "numpy>=2" "optuna>=4" "huggingface-hub>=0.27" \
  "tomli-w>=1.0" "langdetect>=1.0.9"
fsociety doctor   # engine reported as unavailable — expected
```

## 3. Compatibility guarantees

* `import heretic`, `python -m heretic.main` and the `heretic` command work as before.
* Heretic 2.x configuration files remain valid and are not rewritten by the platform.
* Heretic scorer/modifier plugins load unchanged. Platform plugins are a **different**
  contract (`fsociety.plugins`, API version 1) and are documented separately.
* Reproducibility manifests (`reproduce.json`) and the Grimoire workflow are untouched.
* Model modification algorithms are unchanged.

The platform's engine adapter (`fsociety engine run -- …`) enforces the platform trust
policy for `trust_remote_code`. Direct `heretic` usage retains the upstream interactive
confirmation prompt.

## 4. New workflow (optional)

```bash
fsociety config init                    # create fsociety.toml
fsociety hardware                       # real detection with provenance
fsociety models list                    # local + HF cache inventory
fsociety models estimate <model>        # analytical memory plan
fsociety optimize run --model <model> --space space.json
fsociety evaluate run --suite knowledge_core --backend transformers --model <model>
fsociety serve                          # API + dashboard
```

Default workspace: `~/.local/share/fsociety` (Linux), `~/Library/Application Support/fsociety`
(macOS), `%LOCALAPPDATA%\fsociety` (Windows). Override with `FSOCIETY_WORKSPACE`.

## 5. Rollback

```bash
pip uninstall -y fsociety-ai
pip install "heretic-llm==2.2.0.dev0"   # or the exact version from requirements-before.txt
```

The platform workspace is independent; keep or delete it manually. The engine does not
modify it.

## 6. Known migration notes

* Do not keep `heretic-llm` and `fsociety-ai` installed simultaneously: both provide the
  `heretic` executable, and file ownership depends on install order.
* If you use a lock file, regenerate it; the build backend and distribution name change.
* Python 3.10+ remains supported for the platform/API profile. The engine profile now
  requires Python 3.11+ because security-fixed dependency releases require it; on 3.10 the
  engine stack is not installed and the CLI reports it as unavailable. Existing 3.10 Heretic
  users can keep `heretic-llm` or upgrade Python.
* Existing model exports are byte-compatible; the platform does not alter serialization.
