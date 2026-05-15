# PaperPilot Runbook

## Daily Flow

```text
scripts/run_candidate_pipeline.sh
  -> runs/YYYY-MM-DD/candidates.json
Codex writes:
  -> runs/YYYY-MM-DD/review.json
python scripts/render_daily_note.py --date YYYY-MM-DD
  -> Paper-Daily/YYYY/YYYY-MM-DD-paper-codex.md
```

`review.json` is the only daily file Codex should write. Markdown output is rendered by
`paperpilot.render_daily_note`.

## Configuration

Tracked defaults live in:

```text
config/paper-daily-config.yaml
config/interest-profile.yaml
config/negative-keywords.yaml
```

Machine-local paths live in ignored `config/local.yaml`. Start from:

```text
config/local.example.yaml
```

`config/local.yaml` is deep-merged over `config/paper-daily-config.yaml`.

## Package Layout

```text
src/paperpilot/    # implementation
scripts/           # compatibility CLI wrappers
config/            # runtime config and prompts
tests/             # regression checks
```

Keep reusable code in `src/paperpilot/`. Keep `scripts/` thin.

## Checks

```bash
scripts/check.sh
```

The check script uses `./.venv/bin/python` by default and adds `src/` to `PYTHONPATH`.
Override with:

```bash
PAPERPILOT_PYTHON=/path/to/python scripts/check.sh
```

## Feedback Contract

Daily notes expose:

```text
- action:: pending
- rating:: pending
- feedback:: pending
```

`scripts/sync_feedback_from_notes.py` writes those values to
`state/paper-feedback.jsonl` as `user_feedback`.

Valid actions:

```text
read | save | later | skip | not_relevant | pending
```

## Seen Papers

`state/seen-papers.txt` is updated from papers that appear in rendered review
sections. The daily review prompt therefore requires every candidate to be
classified exactly once.

## Runtime Artifacts

These stay local and ignored:

```text
.cache/
logs/
runs/
state/
```
