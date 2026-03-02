#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

echo "Checking environment for Lambek Calculus project"
echo

ok=1

check_cmd() {
  local c="$1"
  if command -v "$c" >/dev/null 2>&1; then
    echo "[OK] command found: $c"
  else
    echo "[MISS] command not found: $c"
    ok=0
  fi
}

check_python_import() {
  local mod="$1"
  if python3 - <<PY >/dev/null 2>&1
import importlib
importlib.import_module("$mod")
PY
  then
    echo "[OK] python module import: $mod"
  else
    echo "[WARN] python module import failed: $mod"
  fi
}

check_cmd python3
check_cmd pdflatex

if command -v gs >/dev/null 2>&1; then
  echo "[OK] PNG renderer found: gs"
elif command -v sips >/dev/null 2>&1; then
  echo "[OK] PNG renderer found: sips"
else
  echo "[MISS] PNG renderer missing: need gs (or sips on macOS)"
  ok=0
fi

if command -v dot >/dev/null 2>&1; then
  echo "[OK] Graphviz found: dot"
else
  echo "[WARN] Graphviz dot missing (only route PNG export affected)"
fi

# Optional quick compile check of local scripts
if command -v python3 >/dev/null 2>&1; then
  if python3 -m py_compile chord_grade.py lambek_tree.py tonality_route.py >/dev/null 2>&1; then
    echo "[OK] Python scripts compile"
  else
    echo "[MISS] Python script compile check failed"
    ok=0
  fi
fi

echo
if [[ "$ok" -eq 1 ]]; then
  echo "Environment check passed."
  exit 0
else
  echo "Environment check has missing required items."
  exit 1
fi
