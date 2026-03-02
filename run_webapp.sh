#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
REQ_FILE="$ROOT_DIR/webapp/requirements.txt"

if [[ ! -d "$VENV_DIR" ]]; then
  echo "[webapp] Creating virtual environment at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

if [[ -f "$REQ_FILE" ]]; then
  echo "[webapp] Installing/updating requirements"
  python -m pip install -r "$REQ_FILE"
fi

echo "[webapp] Starting server at http://127.0.0.1:8000"
exec python "$ROOT_DIR/webapp/backend.py"
