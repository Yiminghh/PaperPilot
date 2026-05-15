# PaperPilot

Local Codex-driven daily paper recommendation pipeline.

Code, dependencies, model cache, run artifacts, and state live here:

```text
/Users/hym/PycharmProjects/PaperPilot
```

Readable Markdown notes are written to:

```text
/Users/hym/Library/CloudStorage/OneDrive-std.uestc.edu.cn/Obsidian/3-PaperFlow
```

## MVP Flow

```text
raw.json -> candidates.json -> review.json -> render_daily_note.py -> paper-codex.md
```

Codex should produce `review.json` only. `scripts/render_daily_note.py` is the only writer for daily Markdown notes.

