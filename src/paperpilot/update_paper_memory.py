#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import re
from pathlib import Path
from typing import Any

from .config_utils import DEFAULT_CONFIG, load_config, resolve_path, run_dir
from .paperpilot_utils import candidate_map, clean_text, load_json, load_jsonl, write_jsonl, yaml_quote


SECTION_KEYS = ("must_read", "worth_reading", "later", "skip")
ACTION_PRIORITY = {
    "read": 5,
    "save": 4,
    "later": 3,
    "skip": 2,
    "not_relevant": 2,
    "pending": 1,
    "": 0,
}

def slugify(value: str) -> str:
    text = value.lower().replace(":", "-")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or "unknown"


def paper_slug(canonical_id: str) -> str:
    return slugify(canonical_id)


def topic_slug(tag: str) -> str:
    return slugify(tag)


def review_decisions(review: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out = {}
    for section in SECTION_KEYS:
        for item in review.get(section) or []:
            cid = item.get("canonical_id", "")
            if cid:
                out[cid] = {**item, "section": section}
    return out


def deep_dive_map(review: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {d.get("canonical_id", ""): d for d in review.get("deep_dives") or [] if d.get("canonical_id")}


def latest_feedback(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for rec in sorted(records, key=lambda r: (r.get("date", ""), r.get("canonical_id", ""))):
        cid = rec.get("canonical_id", "")
        if not cid:
            continue
        prev = out.get(cid)
        if prev is None:
            out[cid] = rec
            continue
        prev_score = ACTION_PRIORITY.get(prev.get("user_action", ""), 0)
        score = ACTION_PRIORITY.get(rec.get("user_action", ""), 0)
        if (rec.get("date", ""), score) >= (prev.get("date", ""), prev_score):
            out[cid] = rec
    return out


def merge_paper_record(
    cid: str,
    candidates: dict[str, dict[str, Any]],
    decisions: dict[str, dict[str, Any]],
    dives: dict[str, dict[str, Any]],
    feedback: dict[str, dict[str, Any]],
    today: str,
) -> dict[str, Any]:
    candidate = candidates.get(cid, {})
    decision = decisions.get(cid, {})
    dive = dives.get(cid, {})
    fb = feedback.get(cid, {})
    tags = []
    for source in (decision.get("tags") or [], dive.get("tags") or [], fb.get("tags") or []):
        for tag in source if isinstance(source, list) else []:
            tag = clean_text(tag)
            if tag and tag not in tags:
                tags.append(tag)
    return {
        "canonical_id": cid,
        "title": clean_text(decision.get("title") or dive.get("title") or candidate.get("title") or fb.get("title") or cid),
        "url": clean_text(decision.get("url") or dive.get("url") or candidate.get("url") or fb.get("url")),
        "first_seen": clean_text(candidate.get("published"))[:10] or today,
        "last_seen": today,
        "status": clean_text(fb.get("user_action") or "pending"),
        "system_decision": clean_text(decision.get("decision") or fb.get("system_decision")),
        "source_basis": clean_text(dive.get("source_basis") or "title+abstract"),
        "categories": [clean_text(c) for c in candidate.get("categories") or [] if clean_text(c)],
        "tags": tags,
        "one_line": clean_text(decision.get("one_sentence") or dive.get("tldr")),
        "why_it_matters": clean_text(decision.get("reason") or dive.get("summary_zh")),
        "relevance": clean_text(decision.get("relevance") or dive.get("relevance_to_me")),
        "reusable_ideas": dive.get("reusable_ideas") or ([decision.get("reusable_idea")] if decision.get("reusable_idea") else []),
        "user_feedback": clean_text(fb.get("user_feedback")),
        "feedback_date": clean_text(fb.get("date")),
    }


def render_paper_page(record: dict[str, Any]) -> str:
    tags = [clean_text(t) for t in record.get("tags") or [] if clean_text(t)]
    lines = [
        "---",
        "type: paper-memory",
        f"canonical_id: {yaml_quote(record['canonical_id'])}",
        f"title: {yaml_quote(record['title'])}",
        f"url: {yaml_quote(record.get('url'))}",
        f"first_seen: {yaml_quote(record.get('first_seen'))}",
        f"last_seen: {yaml_quote(record.get('last_seen'))}",
        f"status: {yaml_quote(record.get('status'))}",
        f"system_decision: {yaml_quote(record.get('system_decision'))}",
        f"source_basis: {yaml_quote(record.get('source_basis'))}",
    ]
    if tags:
        lines.append("tags:")
        lines.extend(f"  - {yaml_quote(tag)}" for tag in tags)
    else:
        lines.append("tags: []")
    categories = [clean_text(cat) for cat in record.get("categories") or [] if clean_text(cat)]
    if categories:
        lines.append("categories:")
        lines.extend(f"  - {yaml_quote(cat)}" for cat in categories)
    else:
        lines.append("categories: []")
    lines.extend(
        [
            "---",
            "",
            f"# {record['title']}",
            "",
            "## One-line Thesis",
            "",
            record.get("one_line") or "_TODO: fill after reading._",
            "",
            "## Why It Matters",
            "",
            record.get("why_it_matters") or "_TODO._",
            "",
            "## Relevance",
            "",
            record.get("relevance") or "_TODO._",
            "",
            "## Reusable Ideas",
            "",
        ]
    )
    ideas = [clean_text(x) for x in record.get("reusable_ideas") or [] if clean_text(x)]
    lines.extend([f"- {idea}" for idea in ideas] or ["_TODO._"])
    lines.extend(
        [
            "",
            "## User Feedback",
            "",
            f"- status:: {record.get('status') or 'pending'}",
            f"- feedback_date:: {record.get('feedback_date') or ''}",
            f"- feedback:: {record.get('user_feedback') or ''}",
            "",
            "## Connections",
            "",
            "_Edges are stored in `../edges.jsonl`._",
            "",
        ]
    )
    return "\n".join(lines)


def render_topic_page(topic: str, linked_papers: list[dict[str, Any]]) -> str:
    title = topic.replace("-", " ")
    lines = [
        "---",
        "type: paper-topic",
        f"topic: {yaml_quote(topic)}",
        "---",
        "",
        f"# {title}",
        "",
        "## Papers",
        "",
    ]
    for paper in linked_papers:
        lines.append(f"- [{paper['canonical_id']}]({paper['path']}) - {paper['title']}")
    lines.append("")
    return "\n".join(lines)


def parse_frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        return {}
    try:
        import yaml
    except ImportError as exc:
        raise SystemExit("Missing dependency: pyyaml. Run `python -m pip install pyyaml`.") from exc
    data = yaml.safe_load(text[4:end]) or {}
    return data if isinstance(data, dict) else {}


def load_memory_records(papers_dir: Path) -> list[dict[str, Any]]:
    records = []
    for path in sorted(papers_dir.glob("*.md")):
        meta = parse_frontmatter(path)
        cid = clean_text(meta.get("canonical_id"))
        if not cid:
            continue
        records.append(
            {
                "canonical_id": cid,
                "title": clean_text(meta.get("title") or cid),
                "url": clean_text(meta.get("url")),
                "first_seen": clean_text(meta.get("first_seen")),
                "last_seen": clean_text(meta.get("last_seen")),
                "status": clean_text(meta.get("status")),
                "system_decision": clean_text(meta.get("system_decision")),
                "source_basis": clean_text(meta.get("source_basis")),
                "categories": [clean_text(c) for c in meta.get("categories") or [] if clean_text(c)],
                "tags": [clean_text(t) for t in meta.get("tags") or [] if clean_text(t)],
                "path": f"papers/{path.name}",
            }
        )
    return records


def rebuild_query_pack(root: Path, records: list[dict[str, Any]], max_chars: int) -> None:
    positive = [r for r in records if r.get("status") in {"read", "save"}]
    negative = [r for r in records if r.get("status") in {"skip", "not_relevant"}]
    pending_must = [r for r in records if r.get("system_decision") == "必读"]
    sections = ["# PaperPilot Query Pack\n\n_Auto-generated. Do not edit._\n"]
    if positive:
        sections.append("\n## Positive User Signals\n")
        for r in positive[-12:]:
            sections.append(f"- {r['title']}: {r.get('one_line','')} Tags: {', '.join(r.get('tags') or [])}\n")
    if negative:
        sections.append("\n## Negative User Signals\n")
        for r in negative[-12:]:
            sections.append(f"- {r['title']}: action={r.get('status')} feedback={r.get('user_feedback','')}\n")
    if pending_must:
        sections.append("\n## Recent Must-Read Papers\n")
        for r in pending_must[-12:]:
            sections.append(f"- {r['title']}: {r.get('one_line','')} Relevance: {r.get('relevance','')}\n")
    content = "".join(sections)
    if len(content) > max_chars:
        content = content[: max_chars - 20] + "\n...(truncated)\n"
    (root / "query_pack.md").write_text(content, encoding="utf-8")


def update_memory(
    review_path: Path,
    candidates_path: Path,
    feedback_path: Path,
    memory_root: Path,
    date: str,
    max_query_chars: int,
) -> int:
    review = load_json(review_path)
    candidates_payload = load_json(candidates_path)
    feedback_records = load_jsonl(feedback_path)
    candidates = candidate_map(candidates_payload)
    decisions = review_decisions(review)
    dives = deep_dive_map(review)
    feedback = latest_feedback(feedback_records)
    selected_ids = {i.get("canonical_id") for i in review.get("must_read") or [] if i.get("canonical_id")}
    selected_ids.update(feedback)
    selected_ids.update(dives)

    memory_root.mkdir(parents=True, exist_ok=True)
    papers_dir = memory_root / "papers"
    topics_dir = memory_root / "topics"
    papers_dir.mkdir(exist_ok=True)
    topics_dir.mkdir(exist_ok=True)
    for stale_topic in topics_dir.glob("*.md"):
        stale_topic.unlink()

    today_records = [
        merge_paper_record(cid, candidates, decisions, dives, feedback, date)
        for cid in sorted(selected_ids)
        if cid
    ]
    for record in today_records:
        slug = paper_slug(record["canonical_id"])
        paper_path = papers_dir / f"{slug}.md"
        paper_path.write_text(render_paper_page(record), encoding="utf-8")

    records = load_memory_records(papers_dir)
    topic_links: dict[str, list[dict[str, Any]]] = {}
    edges = []
    for record in records:
        slug = paper_slug(record["canonical_id"])
        for tag in record.get("tags") or []:
            topic = topic_slug(tag)
            if not topic:
                continue
            rel_path = record.get("path") or f"papers/{slug}.md"
            topic_links.setdefault(topic, []).append(
                {"canonical_id": record["canonical_id"], "title": record["title"], "path": rel_path}
            )
            edges.append(
                {
                    "from": f"paper:{slug}",
                    "to": f"topic:{topic}",
                    "type": "relevant_to",
                    "evidence": f"{record.get('system_decision') or 'memory'} on {date}",
                    "date": date,
                }
            )
    for topic, papers in sorted(topic_links.items()):
        (topics_dir / f"{topic}.md").write_text(render_topic_page(topic, papers), encoding="utf-8")

    deduped_edges = {(e["from"], e["to"], e["type"], e["evidence"]): e for e in edges}
    write_jsonl(memory_root / "edges.jsonl", sorted(deduped_edges.values(), key=lambda e: (e["to"], e["from"])))
    index_lines = ["# PaperPilot Memory Index", "", "## Papers", ""]
    for record in records:
        path = record.get("path") or f"papers/{paper_slug(record['canonical_id'])}.md"
        index_lines.append(f"- [{record['title']}]({path}) - {record.get('system_decision') or 'memory'}")
    index_lines.extend(["", "## Topics", ""])
    for topic in sorted(topic_links):
        index_lines.append(f"- [{topic}](topics/{topic}.md)")
    index_lines.append("")
    (memory_root / "index.md").write_text("\n".join(index_lines), encoding="utf-8")
    rebuild_query_pack(memory_root, records, max_chars=max_query_chars)
    return len(records)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--date", default=dt.date.today().isoformat())
    parser.add_argument("--review", default="")
    parser.add_argument("--candidates", default="")
    parser.add_argument("--feedback", default="")
    parser.add_argument("--memory-root", default="")
    parser.add_argument("--max-query-chars", type=int, default=8000)
    args = parser.parse_args()

    config = load_config(args.config)
    today_run_dir = run_dir(config, args.date)
    review_path = Path(args.review).expanduser() if args.review else today_run_dir / "review.json"
    candidates_path = Path(args.candidates).expanduser() if args.candidates else today_run_dir / "candidates.json"
    feedback_path = Path(args.feedback).expanduser() if args.feedback else resolve_path(
        config, "paths.feedback_path", "state/paper-feedback.jsonl"
    )
    memory_root = Path(args.memory_root).expanduser() if args.memory_root else resolve_path(
        config, "paths.memory_dir", "state/paper-memory"
    )
    count = update_memory(
        review_path=review_path,
        candidates_path=candidates_path,
        feedback_path=feedback_path,
        memory_root=memory_root,
        date=args.date,
        max_query_chars=args.max_query_chars,
    )
    print(f"[paperpilot] updated paper memory: {count} papers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
