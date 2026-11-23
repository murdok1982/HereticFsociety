# Installation Guide

## Requirements

| Profile | Needs | Use case |
| :--- | :--- | :--- |
| Platform | Python 3.10+, ~150 MB | Hardware, models, planning, experiments, API, dashboard |
| Engine | Python 3.11+, PyTorch 2.6+, 10–30 GB disk | Model modification, real model evaluation |
| GPU | CUDA/ROCm/MPS build of PyTorch + drivers | Practical model modification |
| Dashboard build | Node.js 20+ | Building `frontend/` |

> **Python 3.10 note:** the platform/API profile works, but engine dependencies are only
> installed on Python 3.11+ because their security floors require it. On 3.10, use the light
> profile or upgrade Python for modification/evaluation.

## Source installation (recommended)

```bash
git clone https://github.com/murdok1982/HereticFsociety.git
cd HereticFsociety

uv venv
uv sync --extra api --extra dev    # engine + API + dev tools
uv run fsociety doctor
```

Without `uv`:

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -U pip
pip install -e ".[api,dev]"
fsociety doctor
```

PyTorch wheels: the repository's `uv` configuration defaults to CPU wheels. For CUDA,
install the matching torch build first (see pytorch.org), then `pip install -e .`:

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
pip install -e ".[api]"
```

## Lightweight installation (no engine)

Useful for an inventory/API host or CI:

```bash
pip install --no-deps fsociety-ai
pip install "fastapi>=0.115,<1.0" "uvicorn[standard]>=0.30,<1.0" \
  "pydantic>=2.9" "pydantic-settings>=2.5" "rich>=13" "psutil>=6" \
  "py-cpuinfo>=9" "numpy>=2" "optuna>=4" "huggingface-hub>=0.27" \
  "tomli-w>=1.0" "langdetect>=1.0.9"
fsociety hardware
fsociety serve
```

Engine commands return an actionable `engine_unavailable` error in this profile.

## Dashboard

```bash
cd frontend
npm install
npm run typecheck
npm run build          # writes frontend/dist
cd ..
fsociety serve         # serves the built dashboard at /
```

Development mode with hot reload:

```bash
# terminal 1
fsociety serve
# terminal 2
cd frontend && npm run dev   # proxies /api to 127.0.0.1:8000
```

## Configuration

```bash
fsociety config init --path fsociety.toml
fsociety config show
```

Remote API access requires a key:

```bash
export FSOCIETY_API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export FSOCIETY_API_HOST=0.0.0.0
export FSOCIETY_API_ALLOWED_HOSTS='["gpu-box.example.internal"]'
fsociety serve
```

Never commit the key. Terminate TLS at a reverse proxy; do not expose uvicorn directly.

## Verification

```bash
fsociety doctor
fsociety hardware --json | head
fsociety models list
fsociety evaluate suites
fsociety security findings
curl -s http://127.0.0.1:8000/api/v1/health
```

## Uninstall / rollback

See [MIGRATION.md](MIGRATION.md#5-rollback).
