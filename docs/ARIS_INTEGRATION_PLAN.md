# ARIS Pattern Notes

PaperPilot borrows only the ARIS patterns that fit a lightweight daily paper
radar. It does not depend on Claude Code or the full ARIS workflow.

## Adopted

| Pattern | PaperPilot implementation |
|---|---|
| Artifact contract | `raw.json -> candidates.json -> review.json -> paper-codex.md` |
| Local memory | `state/paper-memory/` |
| Verification gate | `paperpilot.verify_papers` |
| Feedback loop | `paperpilot.sync_feedback_from_notes` and weekly report |
| Source basis | `source_basis` field in deep dives |

## Not Adopted

| ARIS feature | Reason |
|---|---|
| Full research entity graph | Too heavy for daily recommendation. |
| Automatic config mutation | Feedback should propose changes, not silently rewrite policy. |
| Claude-only workflow | PaperPilot must remain usable with Codex. |
| Multi-source expansion by default | arXiv-only is simpler and auditable. |

## Current Rule

Add ARIS ideas only when they reduce daily maintenance cost or prevent a real
failure mode. Otherwise keep the pipeline small.
