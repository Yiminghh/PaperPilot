#!/usr/bin/env zsh
set -euo pipefail

PROJECT="/Users/hym/PycharmProjects/PaperPilot"
OUTPUT="/Users/hym/Library/CloudStorage/OneDrive-std.uestc.edu.cn/Obsidian/3-PaperFlow"
TODAY="${PAPERFLOW_DATE:-$(date +%F)}"
PYTHON="${PAPERFLOW_PYTHON:-python3}"
EMBED_PROVIDER="${PAPERFLOW_EMBED_PROVIDER:-local}"

cd "$PROJECT"
mkdir -p "runs/$TODAY" "logs" "state" ".cache/huggingface"

export HF_HOME="$PROJECT/.cache/huggingface"
export TRANSFORMERS_CACHE="$PROJECT/.cache/huggingface"
export PYTHONUNBUFFERED=1

"$PYTHON" scripts/sync_feedback_from_notes.py \
  2>&1 | tee "logs/$TODAY-feedback.log"

"$PYTHON" scripts/paperpilot_fetch_candidates.py \
  --config config/paper-daily-config.yaml \
  --interest config/interest-profile.yaml \
  --negative config/negative-keywords.yaml \
  --output "runs/$TODAY/candidates.json" \
  --raw-output "runs/$TODAY/raw.json" \
  --obsidian-output "$OUTPUT" \
  --embedding-provider "$EMBED_PROVIDER" \
  --date "$TODAY" \
  2>&1 | tee "logs/$TODAY-fetch.log"

echo "[paperpilot] candidates ready: runs/$TODAY/candidates.json"
