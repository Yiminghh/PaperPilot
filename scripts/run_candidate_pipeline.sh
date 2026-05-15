#!/usr/bin/env zsh
set -euo pipefail

SCRIPT_PATH="${0:A}"
SCRIPT_DIR="${SCRIPT_PATH:h}"
PROJECT="${PAPERPILOT_PROJECT:-${SCRIPT_DIR:h}}"
CONFIG="${PAPERPILOT_CONFIG:-config/paper-daily-config.yaml}"
TODAY="${PAPERPILOT_DATE:-$(date +%F)}"
PYTHON="${PAPERPILOT_PYTHON:-python3}"
EMBED_PROVIDER="${PAPERPILOT_EMBED_PROVIDER:-local}"

cd "$PROJECT"
CONFIG_PATHS=("${(@f)$("$PYTHON" scripts/config_utils.py --config "$CONFIG" --path \
  --get paths.runs_dir \
  --get paths.logs_dir \
  --get paths.state_dir \
  --get paths.hf_cache_dir)}")
RUNS_DIR="${CONFIG_PATHS[1]}"
LOGS_DIR="${CONFIG_PATHS[2]}"
STATE_DIR="${CONFIG_PATHS[3]}"
HF_CACHE="${CONFIG_PATHS[4]}"
RUN_DIR="$RUNS_DIR/$TODAY"
mkdir -p "$RUN_DIR" "$LOGS_DIR" "$STATE_DIR" "$HF_CACHE"

export HF_HOME="$HF_CACHE"
export TRANSFORMERS_CACHE="$HF_CACHE"
export PYTHONUNBUFFERED=1

INCLUDE_SEEN_ARGS=()
if [[ "${PAPERPILOT_INCLUDE_SEEN:-}" == "1" || "${PAPERPILOT_INCLUDE_SEEN:-}" == "true" ]]; then
  INCLUDE_SEEN_ARGS=(--include-seen)
fi

"$PYTHON" scripts/sync_feedback_from_notes.py \
  --config "$CONFIG" \
  2>&1 | tee "$LOGS_DIR/$TODAY-feedback.log"

"$PYTHON" scripts/paperpilot_fetch_candidates.py \
  --config "$CONFIG" \
  --interest config/interest-profile.yaml \
  --negative config/negative-keywords.yaml \
  --output "$RUN_DIR/candidates.json" \
  --raw-output "$RUN_DIR/raw.json" \
  --embedding-provider "$EMBED_PROVIDER" \
  --date "$TODAY" \
  "${INCLUDE_SEEN_ARGS[@]}" \
  2>&1 | tee "$LOGS_DIR/$TODAY-fetch.log"

"$PYTHON" scripts/verify_papers.py \
  --config "$CONFIG" \
  --date "$TODAY" \
  --trust-arxiv-source \
  --write-back \
  2>&1 | tee "$LOGS_DIR/$TODAY-verify.log"

echo "[paperpilot] candidates ready: $RUN_DIR/candidates.json"
