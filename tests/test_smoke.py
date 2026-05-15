from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


def test_verify_arxiv_batch_strips_version(monkeypatch):
    import verify_papers

    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>http://arxiv.org/abs/2605.12823v2</id>
        <title>  Example   Paper  </title>
      </entry>
    </feed>
    """

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return xml

    monkeypatch.setattr(verify_papers.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
    result = verify_papers.fetch_arxiv_batch(["2605.12823"], timeout=5)
    assert result["2605.12823"]["title"] == "Example Paper"


def test_sync_feedback_parses_rating(tmp_path):
    from sync_feedback_from_notes import parse_note

    note = tmp_path / "2026-05-15-paper-codex.md"
    note.write_text(
        """---
date: 2026-05-15
---

## 分档推荐

## 今日必读

### A Paper

- canonical_id:: arxiv:2605.12823
- url:: https://arxiv.org/abs/2605.12823
- decision:: 必读
- action:: read
- rating:: 5
- feedback:: useful
""",
        encoding="utf-8",
    )
    records = parse_note(note)
    assert records == [
        {
            "date": "2026-05-15",
            "canonical_id": "arxiv:2605.12823",
            "title": "A Paper",
            "system_decision": "必读",
            "user_action": "read",
            "user_rating": "5",
            "user_reason": "useful",
            "user_feedback": "useful",
            "url": "https://arxiv.org/abs/2605.12823",
            "tags": [],
        }
    ]


def test_review_schema_accepts_current_review():
    import jsonschema

    schema = json.loads((ROOT / "config/review.schema.json").read_text(encoding="utf-8"))
    review = json.loads((ROOT / "runs/2026-05-15/review.json").read_text(encoding="utf-8"))
    jsonschema.validate(review, schema)
