# PaperPilot Maintenance Plan

## Current Shape

PaperPilot is a local-first daily paper radar.

Stable code paths:

- Candidate generation: `paperpilot.paperpilot_fetch_candidates`
- Verification: `paperpilot.verify_papers`
- Review rendering: `paperpilot.render_daily_note`
- Feedback sync: `paperpilot.sync_feedback_from_notes`
- Memory update: `paperpilot.update_paper_memory`

Compatibility wrappers remain in `scripts/` so existing commands keep working.

## Contracts

- Python code owns deterministic data flow.
- Codex owns only `review.json`.
- Markdown notes are rendered, not hand-written.
- Obsidian receives readable Markdown only.
- Local state, logs, caches, runs, and memory stay outside Obsidian.
- `source_basis` must state `title+abstract`, `html`, `pdf`, or `html+pdf`.
- Unverified papers cannot be promoted to `must_read`.

## Configuration Boundary

- `config/paper-daily-config.yaml`: tracked defaults, retrieval policy, boost rules.
- `config/local.yaml`: ignored machine paths.
- `config/interest-profile.yaml`: positive interests and query expansion.
- `config/negative-keywords.yaml`: hard excludes, soft downweights, context exceptions.

Keep policy terms in YAML. Code should implement generic matching only.

## Review Coverage

`state/seen-papers.txt` is based on rendered review sections. To avoid repeat
candidates, Codex must classify every candidate exactly once into:

```text
must_read | worth_reading | later | skip
```

## Near-Term Work

1. Run the daily flow for one week.
2. Watch arXiv rate-limit stability.
3. Check whether `final_top_k` creates too much review load.
4. Use weekly feedback reports before changing retrieval weights.
5. Add tests only when a bug or rule change appears.

## Avoid

- Do not add new sources before the arXiv-only flow is stable.
- Do not auto-edit interest config from feedback reports.
- Do not move caches or models into Obsidian or OneDrive.
- Do not let Codex write the final daily Markdown directly.
