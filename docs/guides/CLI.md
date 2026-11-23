# CLI Reference

Run `fsociety --help` or `fsociety <command> --help`. Global options must precede the
subcommand:

```bash
fsociety [--json] [--workspace DIR] [--config FILE] <command> ...
```

`--json` prints a machine-readable payload (errors included). Exit codes: `0` success,
`1` failure, `2` usage error, `130` interrupted.

## `version`

Platform version, plugin API version, workspace and engine availability.

```bash
fsociety version
fsociety --json version
```

## `doctor`

Validates Python, workspace writability, the SQLite store, hardware detection, engine
availability, dashboard build, API key configuration, trust policy and plugin discovery.
Exit code `1` when a check is `fail` (warnings do not fail).

```bash
fsociety doctor
fsociety --json doctor > doctor.json
```

## `hardware`

Real detection with provenance (`measured`/`reported`) and a stable fingerprint.
`--refresh` bypasses the 10-minute cache.

```bash
fsociety hardware
fsociety --json hardware | jq '.gpus, .backends'
```

## `models`

| Command | Purpose |
| :--- | :--- |
| `list` | Local models directory + Hugging Face cache (`--no-hf-cache`, `--limit`) |
| `scan` | Scan explicit roots (`--path` repeatable) |
| `inspect` | Metadata, exact safetensors parameter count, compatibility (`--hash` for file digests) |
| `verify` | SHA-256 manifest verification (`--manifest`) |
| `estimate` | Analytical memory plan (`--batch-size`, `--sequence-length`, `--dtype`, `--quantization`) |
| `download` | Download a HF snapshot (`--revision`, `--pattern`, `--weights` required for weight files) |

```bash
fsociety models list
fsociety models inspect Qwen/Qwen3-4B-Instruct-2507 --json
fsociety models estimate ./models/my-model --batch-size 4 --sequence-length 8192
fsociety models verify ./models/my-model --manifest exports/model/manifest.json
fsociety models download Qwen/Qwen3-4B-Instruct-2507 --revision main --weights
```

`inspect` never imports torch. Parameter counts are exact when safetensors headers are
present; a `.bin`-only model produces an estimated count with a warning.

## `optimize`

Runs a **real** seeded Optuna search. The built-in objective minimizes estimated memory
and prefers configurations that fit the detected hardware. The search space must vary at
least one of `dtype`, `quantization`, `batch_size`, `sequence_length`.

```bash
cat > space.json <<'JSON'
{"dimensions": [
  {"name": "dtype", "kind": "categorical", "choices": ["bfloat16", "float16"]},
  {"name": "batch_size", "kind": "int", "low": 1, "high": 16},
  {"name": "sequence_length", "kind": "categorical", "choices": [2048, 4096, 8192]}
]}
JSON
fsociety optimize run --model ./models/my-model --space space.json --trials 30 --seed 42
```

Every trial is persisted (`fsociety experiments trials <id>`). Figures are estimates.

## `evaluate`

| Command | Purpose |
| :--- | :--- |
| `suites` | List bundled smoke suites with provenance |
| `run` | Execute a suite with a real backend |
| `report` | Print/export a stored report (`--experiment` or `--file`) |
| `compare` | Compare two report JSON files with explicit comparability rules |

```bash
fsociety evaluate suites
fsociety evaluate run --suite math_word_problems \
  --backend transformers --model Qwen/Qwen3-4B-Instruct-2507 \
  --seed 42 --max-new-tokens 256 --limit 20
fsociety evaluate report --experiment exp_...
fsociety evaluate compare baseline.json candidate.json --threshold 0.02 --output cmp.json
```

Transformers and llama.cpp backends require their runtimes; without them the command
fails with `engine_unavailable` instead of fabricating output. `--temperature 0` uses
deterministic decoding.

## `experiments`

```bash
fsociety experiments list --limit 20
fsociety experiments show exp_...
fsociety experiments trials exp_...
fsociety experiments export --output experiments.json
```

## `plugins`

Platform plugins (distinct from Heretic scorer/modifier plugins).

```bash
fsociety plugins list
fsociety plugins validate ./plugins/my_plugin.py   # imports code: trust required
fsociety --json plugins run code_quality --method score \
  --payload '{"text":"def f(x): return x + 1"}'
```

Execution is isolated by default (subprocess). `--in-process` is an explicit escape hatch.

## `security`

```bash
fsociety security policy
fsociety security findings            # deployment configuration findings
fsociety security audit --path .      # secret-like files + policy + dependency audit
fsociety security sbom --output sbom.json
fsociety security deps                # requires pip-audit; reports "unavailable" otherwise
```

`security audit` exits `1` when secret-like content is found. It reports paths, never
values.

## `export`

```bash
fsociety export bundle \
  --file models/my-model/config.json \
  --file models/my-model/model.safetensors \
  --destination my-bundle \
  --meta model=my-model --meta license=apache-2.0
```

Creates a staging directory with `manifest.json` (sha256 + size per file), `README.md` and
the original files. Destination is constrained to the workspace `exports/` directory.

## `serve`

```bash
fsociety serve                          # 127.0.0.1:8000
fsociety serve --host 0.0.0.0 --port 9000
```

Serves `/api/v1`, OpenAPI docs at `/api/docs` (when enabled) and the built dashboard at
`/`. Remote clients require `FSOCIETY_API_KEY`.

## `config`

```bash
fsociety config init --path fsociety.toml
fsociety config show
```

## `engine`

Adapter for the preserved Heretic engine.

```bash
fsociety engine status
fsociety engine run -- Qwen/Qwen3-4B-Instruct-2507
```

The adapter validates arguments, writes a valid Heretic 2.x TOML config, enforces the
platform trust policy and invokes `python -m heretic.main` without a shell. The original
`heretic` command remains available and unchanged.
