#!/usr/bin/env bash
# FSOCIETY AI installer for Linux/macOS source checkouts.
# Usage:
#   ./deployment/scripts/install.sh            # engine + API + dev tools
#   ./deployment/scripts/install.sh --light    # API/inventory only (no torch)
#   ./deployment/scripts/install.sh --no-dashboard
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

MODE="full"
BUILD_DASHBOARD=1
for arg in "$@"; do
  case "$arg" in
    --light) MODE="light" ;;
    --no-dashboard) BUILD_DASHBOARD=0 ;;
    -h|--help)
      sed -n '2,8p' "$0"
      exit 0
      ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "error: python3 not found" >&2
  exit 1
fi

if command -v uv >/dev/null 2>&1; then
  echo "==> Using uv"
  uv venv --allow-existing
  if [ "$MODE" = "light" ]; then
    uv pip install -r deployment/docker/requirements-platform.txt pytest ruff
    uv pip install --no-deps -e .
  else
    uv pip install -e ".[api,dev]"
  fi
  RUN="uv run"
else
  echo "==> Using pip/venv"
  "$PYTHON_BIN" -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  python -m pip install -U pip
  if [ "$MODE" = "light" ]; then
    python -m pip install -r deployment/docker/requirements-platform.txt pytest ruff
    python -m pip install --no-deps -e .
  else
    python -m pip install -e ".[api,dev]"
  fi
  RUN=""
fi

if [ "$BUILD_DASHBOARD" = "1" ]; then
  if command -v npm >/dev/null 2>&1; then
    echo "==> Building dashboard"
    (cd frontend && npm install && npm run build)
  else
    echo "==> npm not found; skipping dashboard build (API still works)"
  fi
fi

echo "==> Verifying"
$RUN fsociety doctor || true
echo
echo "Next:"
echo "  $RUN fsociety serve"
echo "  open http://127.0.0.1:8000"
