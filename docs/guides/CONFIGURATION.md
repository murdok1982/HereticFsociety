# Configuration Reference

FSOCIETY AI has **two separate** configuration systems:

1. **Platform** — `fsociety.toml` / `FSOCIETY_*` environment (this document).
2. **Engine** — Heretic's own `config.toml` / `HERETIC_CONFIG_FILE` (unchanged; see
   `config.default.toml` in the repository).

The platform never rewrites your Heretic configuration.

## Precedence

```
defaults  →  fsociety.toml  →  FSOCIETY_* environment variables  →  CLI flags
```

`--config FILE` or `FSOCIETY_CONFIG` selects a file; otherwise `./fsociety.toml` is used
when present. Both flat keys and a `[fsociety]` table are accepted.

```bash
fsociety config init --path fsociety.toml
fsociety --config fsociety.toml config show
```

## Settings

| Setting | Env variable | Default | Description |
| :--- | :--- | :--- | :--- |
| `workspace` | `FSOCIETY_WORKSPACE` | platform data dir | Root for data, models, reports, exports, logs |
| `models_dir` | `FSOCIETY_MODELS_DIR` | `<workspace>/models` | Local model directories |
| `hf_cache_dir` | `FSOCIETY_HF_CACHE_DIR` | HF default | Hugging Face cache to scan |
| `default_dtype` | `FSOCIETY_DEFAULT_DTYPE` | `auto` | Planning default (`auto`/`float32`/`float16`/`bfloat16`) |
| `default_quantization` | `FSOCIETY_DEFAULT_QUANTIZATION` | `none` | `none` or `bnb_4bit` |
| `default_sequence_length` | `FSOCIETY_DEFAULT_SEQUENCE_LENGTH` | `2048` | Planning context length |
| `default_batch_size` | `FSOCIETY_DEFAULT_BATCH_SIZE` | `1` | Planning batch size |
| `offline` | `FSOCIETY_OFFLINE` | `false` | Disable Hub network access |
| `evaluation_seed` | `FSOCIETY_EVALUATION_SEED` | `42` | Default evaluation seed |
| `max_new_tokens` | `FSOCIETY_MAX_NEW_TOKENS` | `256` | Default generation limit |
| `temperature` | `FSOCIETY_TEMPERATURE` | `0.0` | `0` = deterministic decoding |
| `trust_remote_code` | `FSOCIETY_TRUST_REMOTE_CODE` | `never` | `never`/`allowlist`/`always` |
| `trusted_repositories` | `FSOCIETY_TRUSTED_REPOSITORIES` | `[]` | Exact `org/name` allowlist |
| `plugin_isolation` | `FSOCIETY_PLUGIN_ISOLATION` | `true` | Run plugins in subprocesses |
| `plugin_timeout_seconds` | `FSOCIETY_PLUGIN_TIMEOUT_SECONDS` | `30` | Per-run plugin timeout |
| `plugin_allow_network` | `FSOCIETY_PLUGIN_ALLOW_NETWORK` | `false` | Require network for plugins |
| `plugin_max_output_bytes` | `FSOCIETY_PLUGIN_MAX_OUTPUT_BYTES` | `1000000` | Plugin stdout cap |
| `allow_absolute_model_paths` | `FSOCIETY_ALLOW_ABSOLUTE_MODEL_PATHS` | `true` | CLI/local absolute model paths |
| `api_host` | `FSOCIETY_API_HOST` | `127.0.0.1` | Bind address |
| `api_port` | `FSOCIETY_API_PORT` | `8000` | Bind port |
| `api_key` | `FSOCIETY_API_KEY` | unset | Required for non-loopback clients |
| `api_require_key_for_loopback` | `FSOCIETY_API_REQUIRE_KEY_FOR_LOOPBACK` | `false` | Also protect localhost |
| `api_cors_origins` | `FSOCIETY_API_CORS_ORIGINS` | `[]` | Explicit origins; `*` rejected |
| `api_allowed_hosts` | `FSOCIETY_API_ALLOWED_HOSTS` | `[]` | Extra TrustedHost entries |
| `api_rate_limit_per_minute` | `FSOCIETY_API_RATE_LIMIT_PER_MINUTE` | `120` | Per-client sliding window |
| `api_max_upload_bytes` | `FSOCIETY_API_MAX_UPLOAD_BYTES` | `1000000` | Request body cap |
| `api_docs_enabled` | `FSOCIETY_API_DOCS_ENABLED` | `true` | `/api/docs` + OpenAPI |
| `serve_frontend` | `FSOCIETY_SERVE_FRONTEND` | `true` | Serve `frontend/dist` if present |
| `frontend_dist` | `FSOCIETY_FRONTEND_DIST` | auto | Explicit dashboard build path |
| `log_level` | `FSOCIETY_LOG_LEVEL` | `INFO` | Console level |
| `audit_log_enabled` | `FSOCIETY_AUDIT_LOG_ENABLED` | `true` | Write `logs/audit.jsonl` |

List-valued env variables use JSON syntax:

```bash
export FSOCIETY_TRUSTED_REPOSITORIES='["org/model-a","org/model-b"]'
export FSOCIETY_API_CORS_ORIGINS='["http://localhost:5173"]'
```

## Example `fsociety.toml`

```toml
[fsociety]
workspace = "/srv/fsociety"
default_dtype = "bfloat16"
default_sequence_length = 4096
offline = false
evaluation_seed = 42

trust_remote_code = "allowlist"
trusted_repositories = ["my-org/trusted-model"]
plugin_isolation = true
plugin_allow_network = false

api_host = "127.0.0.1"
api_port = 8000
api_rate_limit_per_minute = 120
```

## Secrets

`api_key` is a `SecretStr`: it is never included in `config show`, the settings endpoint,
logs or reports. Prefer an environment variable or secret manager over a TOML file, and
restrict the file to `chmod 600`. `HF_TOKEN` is consumed by `huggingface_hub` directly;
the platform never prints it.

## Trust policy

* `never` (default): remote model code is always denied by the platform adapter.
* `allowlist`: only exact repository ids in `trusted_repositories` may use remote code.
* `always`: every repository may execute code; this produces a HIGH security finding and
  should only be used in fully controlled research environments.

The original `heretic` command keeps its interactive confirmation behaviour.

## Workspace layout

```
<workspace>/
├── data/fsociety.sqlite3
├── models/
├── reports/
├── exports/
├── audits/
├── plugins/
├── cache/
└── logs/audit.jsonl
```
