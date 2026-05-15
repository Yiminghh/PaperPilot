#!/usr/bin/env zsh
set -euo pipefail

SCRIPT_PATH="${0:A}"
PROJECT="${SCRIPT_PATH:h:h}"
PYTHON="${PAPERPILOT_PYTHON:-$PROJECT/.venv/bin/python}"

cd "$PROJECT"

"$PYTHON" -m py_compile scripts/*.py
zsh -n scripts/run_candidate_pipeline.sh
"$PYTHON" -m ruff check scripts tests
"$PYTHON" -m pytest -q
