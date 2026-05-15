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

"$PYTHON" - <<'PY'
import json
from pathlib import Path
import jsonschema

schema = json.loads(Path("config/review.schema.json").read_text(encoding="utf-8"))
review = json.loads(Path("runs/2026-05-15/review.json").read_text(encoding="utf-8"))
jsonschema.validate(review, schema)
print("review schema ok")
PY
