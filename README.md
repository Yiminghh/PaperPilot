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
raw.json -> candidates.json -> initial review.json -> enriched.json -> final review.json -> render_daily_note.py -> paper-codex.md
```

Codex should produce `review.json` only. `scripts/render_daily_note.py` is the only writer for daily Markdown notes.

`scripts/run_candidate_pipeline.sh` runs the deterministic pre-review path only:

```text
feedback sync -> arXiv fetch -> BM25/embedding retrieval -> verification -> candidates.json
```

The full daily note still requires Codex to write `review.json` and then run `scripts/render_daily_note.py`.

## Configuration

Runtime paths are centralized in:

```text
config/paper-daily-config.yaml
```

The `paths` section controls project-local state, logs, run artifacts, Hugging Face cache, and Obsidian output directories. CLI arguments override config values when provided.

Interest and negative filters are intentionally split:

- `config/interest-profile.yaml`: positive research interests and query expansion only.
- `config/negative-keywords.yaml`: hard excludes and soft downweight terms only.

Useful checks:

```bash
python scripts/config_utils.py --get paths.paperflow_root --path
python scripts/config_utils.py --get paths.hf_cache_dir --path
```

Embedding defaults to local `BAAI/bge-m3`. OpenAI-compatible embedding APIs are available only when explicitly enabled:

```bash
PAPERPILOT_EMBED_PROVIDER=api \
PAPERPILOT_EMBED_BASE_URL=https://api.openai.com/v1 \
PAPERPILOT_EMBED_MODEL=text-embedding-3-large \
PAPERPILOT_EMBED_API_KEY=... \
scripts/run_candidate_pipeline.sh
```
