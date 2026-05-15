#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

from config_utils import DEFAULT_CONFIG, load_config, resolve_path
from paperpilot_utils import load_jsonl, write_jsonl


FIELD_RE = re.compile(r"^-\s*([A-Za-z_]+)::\s*(.*)$")
HEADING_RE = re.compile(r"^###\s+(.+)$")
HEADING_NUMBER_RE = re.compile(r"^\d+\.\s+")
H2_RE = re.compile(r"^##\s+(.+)$")
RANKED_SECTION_TITLES = {"今日必读", "值得看", "稍后", "跳过"}


def parse_frontmatter_date(text: str, fallback: str) -> str:
    if not text.startswith("---"):
        return fallback
    end = text.find("\n---", 3)
    if end < 0:
        return fallback
    for line in text[3:end].splitlines():
        if line.strip().startswith("date:"):
            return line.split(":", 1)[1].strip()
    return fallback


def parse_note(path: Path, include_pending: bool = False) -> list[dict[str, Any]]:
    content = path.read_text(encoding="utf-8")
    date = parse_frontmatter_date(content, path.name[:10])
    records: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    in_ranked_area = False
    current_section = ""
    for line in content.splitlines():
        h2 = H2_RE.match(line)
        if h2:
            title = h2.group(1).strip()
            if current:
                records.append(current)
                current = None
            if title == "分档推荐":
                in_ranked_area = True
                current_section = ""
                continue
            if in_ranked_area and title in RANKED_SECTION_TITLES:
                current_section = title
                continue
            if in_ranked_area:
                in_ranked_area = False
                current_section = ""
            continue
        h = HEADING_RE.match(line)
        if h:
            if not in_ranked_area or not current_section:
                continue
            if current:
                records.append(current)
            title = HEADING_NUMBER_RE.sub("", h.group(1).strip(), count=1)
            current = {"date": date, "title": title, "section": current_section}
            continue
        if current is None:
            continue
        m = FIELD_RE.match(line)
        if m:
            current[m.group(1).strip()] = m.group(2).strip()
    if current:
        records.append(current)
    out = []
    for rec in records:
        cid = rec.get("canonical_id", "")
        action = rec.get("action", "pending")
        rating = rec.get("rating", "pending")
        feedback = rec.get("feedback", "pending")
        if not cid:
            continue
        if not include_pending and action == "pending" and rating == "pending" and feedback == "pending":
            continue
        out.append(
            {
                "date": rec.get("date", date),
                "canonical_id": cid,
                "title": rec.get("title", ""),
                "system_decision": rec.get("decision") or rec.get("section", ""),
                "user_action": action,
                "user_rating": rating,
                "user_reason": feedback,
                "user_feedback": feedback,
                "url": rec.get("url", ""),
                "tags": [x.strip() for x in rec.get("tags", "").split(",") if x.strip()],
            }
        )
    return out


def load_existing(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (rec.get("date", ""), rec.get("canonical_id", "")): rec
        for rec in load_jsonl(path)
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--notes-dir", default="")
    parser.add_argument("--feedback-path", default="")
    args = parser.parse_args()

    config = load_config(args.config)
    notes_dir = Path(args.notes_dir).expanduser() if args.notes_dir else resolve_path(config, "paths.daily_notes_dir")
    feedback_path = Path(args.feedback_path).expanduser() if args.feedback_path else resolve_path(
        config, "paths.feedback_path", "state/paper-feedback.jsonl"
    )
    feedback_path.parent.mkdir(parents=True, exist_ok=True)
    records = load_existing(feedback_path)
    for path in sorted(notes_dir.glob("*/*-paper-codex.md")):
        for rec in parse_note(path):
            records[(rec["date"], rec["canonical_id"])] = rec
    ordered = sorted(records.values(), key=lambda r: (r.get("date", ""), r.get("canonical_id", "")))
    write_jsonl(feedback_path, ordered)
    print(f"[paperpilot] synced {len(ordered)} feedback records to {feedback_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
