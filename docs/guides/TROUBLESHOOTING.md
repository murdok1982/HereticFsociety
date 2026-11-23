# Troubleshooting

## `engine_unavailable` on model modification or evaluation

The platform core runs without PyTorch; modification and Transformers evaluation need the
engine stack.

```bash
fsociety --json doctor          # shows what is missing
pip install -e ".[api]"         # source checkout (installs torch/transformers)
# or
pip install -U fsociety-ai
```

In a light deployment this error is expected. Use inventory, hardware, planning, the API
and the dashboard; point evaluation at another machine with the engine.

## `torch` installed but CUDA is unavailable

`fsociety hardware` distinguishes **driver** (`nvidia-smi`) from **runtime**
(`torch.cuda.is_available()`). A CUDA driver without a CUDA-enabled torch build reports
`cuda_driver: true` and `cuda_torch: false`. Install the matching wheel:

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
```

The platform never assumes that an OS or a GPU name implies working acceleration.

## API responds `403 remote_access_disabled`

The API binds to loopback by default. Non-loopback clients are refused unless a key is
configured:

```bash
export FSOCIETY_API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export FSOCIETY_API_ALLOWED_HOSTS='["your-hostname"]'
fsociety serve
curl -H "X-API-Key: $FSOCIETY_API_KEY" http://your-hostname:8000/api/v1/health
```

`400 Invalid host header` means the `Host` header is not in the trusted list. Add the exact
hostname to `api_allowed_hosts`; do not use `*`.

## Dashboard shows "API unreachable"

* Is `fsociety serve` running? Check `/api/v1/health` with `curl`.
* Vite dev server proxies `/api` to `127.0.0.1:8000`; make sure the API uses that port.
* If the API requires a key, enter it in Settings (stored in `sessionStorage`).
* A production build must be served by FastAPI (`frontend/dist` present) or your own static
  host with a reverse proxy for `/api`.

## Dashboard is missing (`/` returns JSON)

Build the frontend:

```bash
cd frontend && npm install && npm run build
```

Alternatively set `FSOCIETY_FRONTEND_DIST` to the build directory. The API is fully
functional without the dashboard.

## `unsafe_path` errors

All user paths are resolved inside the workspace. Absolute model paths are allowed only
inside the workspace or the configured models directory. Relative paths with `..`, absolute
paths in scan/export fields, and symlinks that escape the workspace are rejected by design.

## Model has an unknown parameter count

* `.bin`-only models cannot be counted without torch; the count is marked `estimated` with
  a warning. Prefer safetensors.
* GGUF is detected but not parsed for parameters. Use llama.cpp tooling for GGUF metadata.
* Corrupt `config.json` produces warnings, not a crash; run `fsociety models inspect <ref>`
  to see them.

## Downloads fail or `offline` is set

`FSOCIETY_OFFLINE=true` disables Hub network access. Unset it, authenticate with
`huggingface-cli login` (or `HF_TOKEN`), and pin a revision. Weight downloads require
`--weights` on the CLI; this is intentional.

## Plugin timeout / crash

```bash
fsociety plugins list
fsociety plugins validate ./plugins/my_plugin.py
```

Plugins run isolated with a timeout (`plugin_timeout_seconds`) and output cap. Increase the
timeout only for trusted code. A crash is recorded in the audit log and does not kill the
API. `--in-process` exists as an explicit escape hatch and removes isolation.

## Evaluation says "incomparable"

Expected when suite hash, grader version or evaluation configuration differ. Re-run both
models with identical seed/temperature/max tokens/limit, or compare within the same suite
version. Hardware differences appear as warnings; do not compare performance metrics across
machines.

## SQLite schema conflict after downgrade

The store fails closed when the schema version is newer than the code. Back up
`data/fsociety.sqlite3`, then either upgrade the package or start a new workspace. There is
no destructive automatic migration.

## exFAT/network volume problems

Some volumes do not support symlinks or POSIX permissions. The repository may appear
world-executable (ruff `EXE002`), and `safe_join` symlink tests are skipped. Use a POSIX
filesystem for development and containers. This does not affect the application logic; the
lint policy ignores `EXE002`.

## `nginx`/reverse proxy returns 400

Keep the original `Host` header, add it to `api_allowed_hosts`, terminate TLS at the proxy,
and forward `X-API-Key`. Do not expose uvicorn directly to the internet.

## Jobs stay queued

The job pool has two worker threads per process. Long evaluation/modification jobs occupy a
slot. Check `GET /api/v1/jobs`, cancel stale jobs, or run a separate API instance for heavy
work. Jobs interrupted by an API restart are marked failed.

## Reproducing an engine result

Use Heretic's own `--reproduce path/to/reproduce.json` flow (unchanged). The platform does
not alter manifests or weight serialization.
