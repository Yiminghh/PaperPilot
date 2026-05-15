from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


def sample_candidates() -> dict:
    return {
        "date": "2026-05-15",
        "source": "arxiv",
        "raw_count": 1,
        "recent_count": 1,
        "seen_count": 0,
        "hard_excluded_count": 0,
        "category_policy_excluded_count": 0,
        "candidate_count": 1,
        "embedding_provider": "none",
        "embedding_model": "",
        "candidates": [
            {
                "canonical_id": "arxiv:2605.12823",
                "source": "arxiv",
                "title": "Example Paper",
                "url": "https://arxiv.org/abs/2605.12823",
                "abstract": "A graph AI4Science paper.",
                "categories": ["cs.LG"],
                "verification": {"status": "verified"},
            }
        ],
    }


def sample_review() -> dict:
    decision = {
        "canonical_id": "arxiv:2605.12823",
        "title": "Example Paper",
        "url": "https://arxiv.org/abs/2605.12823",
        "decision": "必读",
        "one_sentence": "这是一篇值得优先阅读的图学习论文。",
        "reason": "它和高阶图学习及 AI4Science 方向直接相关。",
        "relevance": "高阶图学习",
        "reusable_idea": "把结构约束加入生成模型。",
        "uncertainty": "需要核查实验细节。",
        "suggested_action": "先读方法和实验。",
        "tags": ["graph", "AI4Science"],
    }
    return {
        "date": "2026-05-15",
        "summary": "今日候选中图学习和 AI4Science 相关论文更值得优先阅读。",
        "must_read": [decision],
        "worth_reading": [],
        "later": [],
        "skip": [],
        "deep_dives": [
            {
                "canonical_id": "arxiv:2605.12823",
                "title": "Example Paper",
                "url": "https://arxiv.org/abs/2605.12823",
                "source_basis": "title+abstract",
                "tldr": "图学习方法值得先看。",
                "summary_zh": "论文围绕图结构建模展开，适合作为今日优先阅读对象。",
                "problem": "现有图模型对复杂结构表达不足。",
                "method": "使用结构感知表示学习处理图数据。",
                "core_innovations": ["结构感知表示", "面向科学任务的图建模"],
                "evidence": ["摘要声称在图任务上有效。"],
                "limitations": ["还需要阅读全文核查实验。"],
                "relevance_to_me": "与高阶图学习和 AI4Science 兴趣相关。",
                "reusable_ideas": ["把结构先验加入候选生成。"],
                "questions_to_check": ["实验是否覆盖分子或科学数据？"],
                "reading_priority": "今天优先读方法。",
                "tags": ["graph", "AI4Science"],
            }
        ],
        "trend_observations": ["图模型和科学任务结合较多。"],
        "tomorrow_followups": ["继续观察高阶图生成方向。"],
    }


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

### 12. A Paper

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


def test_review_schema_accepts_minimal_review():
    import jsonschema

    schema = json.loads((ROOT / "config/review.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(sample_review(), schema)


def test_render_requires_existing_schema(tmp_path):
    import render_daily_note

    with pytest.raises(SystemExit, match="review schema not found"):
        render_daily_note.validate_review({}, tmp_path / "missing-schema.json")


def test_render_rejects_duplicate_deep_dives():
    import render_daily_note

    review = sample_review()
    candidates = sample_candidates()
    duplicate = dict(review["deep_dives"][0])
    review["deep_dives"] = [review["deep_dives"][0], duplicate]
    with pytest.raises(SystemExit, match="duplicate deep_dive"):
        render_daily_note.validate_review_against_candidates(review, candidates)


def test_render_note_omits_old_navigation_sections():
    import render_daily_note

    review = sample_review()
    candidates = sample_candidates()
    note = render_daily_note.render_note(review, candidates, "2026-05-15")
    assert "## 快速导航" not in note
    assert "## 先读这几篇" not in note
    assert "### 1. " in note


def test_update_seen_marks_only_reviewed_papers(tmp_path):
    import render_daily_note

    seen_path = tmp_path / "seen.txt"
    review = {
        "must_read": [{"canonical_id": "arxiv:1"}],
        "worth_reading": [],
        "later": [],
        "skip": [{"canonical_id": "arxiv:2"}],
        "deep_dives": [{"canonical_id": "arxiv:1"}],
    }
    render_daily_note.update_seen(review, seen_path)
    assert seen_path.read_text(encoding="utf-8").splitlines() == ["arxiv:1", "arxiv:2"]
