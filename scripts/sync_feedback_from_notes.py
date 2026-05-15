#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


FIELD_RE = re.compile(r"^-\s*([A-Za-z_]+)::\s*(.*)$")
HEADING_RE = re.compile(r"^###\s+(.+)$")


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


def parse_note(path: Path) -> list[dict[str, Any]]:
    content = path.read_text(encoding="utf-8")
    date = parse_frontmatter_date(content, path.name[:10])
    records: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in content.splitlines():
        h = HEADING_RE.match(line)
        if h:
            if current:
                records.append(current)
            current = {"date": date, "title": h.group(1).strip()}
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
        feedback = rec.get("feedback", "pending")
        if not cid:
            continue
        if action == "pending" and feedback == "pending":
            continue
        out.append(
            {
                "date": rec.get("date", date),
                "canonical_id": cid,
                "title": rec.get("title", ""),
                "system_decision": rec.get("decision", ""),
                "user_action": action,
                "user_feedback": feedback,
                "url": rec.get("url", ""),
                "tags": [x.strip() for x in rec.get("tags", "").split(",") if x.strip()],
            }
        )
    return out


def load_existing(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    if not path.exists():
        return {}
    records = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        records[(rec.get("date", ""), rec.get("canonical_id", ""))] = rec
    return records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--notes-dir", default="/Users/hym/Library/CloudStorage/OneDrive-std.uestc.edu.cn/Obsidian/3-PaperFlow/Paper-Daily")
    parser.add_argument("--feedback-path", default="state/paper-feedback.jsonl")
    args = parser.parse_args()

    notes_dir = Path(args.notes_dir)
    feedback_path = Path(args.feedback_path)
    feedback_path.parent.mkdir(parents=True, exist_ok=True)
    records = load_existing(feedback_path)
    for path in sorted(notes_dir.glob("*/*-paper-codex.md")):
        for rec in parse_note(path):
            records[(rec["date"], rec["canonical_id"])] = rec
    ordered = sorted(records.values(), key=lambda r: (r.get("date", ""), r.get("canonical_id", "")))
    feedback_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in ordered)
        + ("\n" if ordered else ""),
        encoding="utf-8",
    )
    print(f"[paperpilot] synced {len(ordered)} feedback records to {feedback_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

