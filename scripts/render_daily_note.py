#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any


SECTION_TITLES = {
    "must_read": "今日必读",
    "worth_reading": "值得看",
    "later": "稍后",
    "skip": "跳过",
}

SECTION_CALLOUTS = {
    "must_read": "tip",
    "worth_reading": "note",
    "later": "question",
    "skip": "warning",
}


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def validate_review(review: dict[str, Any], schema_path: Path) -> None:
    schema = load_json(schema_path)
    try:
        import jsonschema
    except ImportError:
        missing = [k for k in schema.get("required", []) if k not in review]
        if missing:
            raise SystemExit(f"review.json missing required keys: {missing}")
        return
    jsonschema.validate(review, schema)


def text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(x) for x in value)
    return str(value).strip()


def table_text(value: Any) -> str:
    return text(value).replace("\n", " ").replace("|", "\\|")


def bullet_lines(values: list[Any]) -> list[str]:
    cleaned = [text(v) for v in values if text(v)]
    if not cleaned:
        return ["- 无。"]
    return [f"- {value}" for value in cleaned]


def callout(kind: str, title: str, body: str | list[str], folded: bool = False) -> list[str]:
    marker = "-" if folded else ""
    lines = [f"> [!{kind}]{marker} {title}"]
    body_lines = body.splitlines() if isinstance(body, str) else body
    if not body_lines:
        body_lines = ["无。"]
    for line in body_lines:
        lines.append(f"> {line}" if line else ">")
    return lines


def candidate_map(candidates: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {p.get("canonical_id", ""): p for p in candidates.get("candidates", [])}


def render_quick_nav(review: dict[str, Any]) -> list[str]:
    lines = [
        "| 优先级 | 论文 | 方向 | 建议动作 |",
        "|---|---|---|---|",
    ]
    for key, heading in SECTION_TITLES.items():
        for item in review.get(key) or []:
            title = table_text(item.get("title"))
            url = text(item.get("url"))
            paper = f"[{title}]({url})" if url else title
            tags = table_text(item.get("tags") or [])
            action = table_text(item.get("suggested_action"))
            lines.append(f"| {heading} | {paper} | {tags} | {action} |")
    return lines


def render_read_first(review: dict[str, Any]) -> list[str]:
    items = (review.get("must_read") or [])[:3]
    if not items:
        return ["无。"]
    lines = []
    for idx, item in enumerate(items, start=1):
        title = text(item.get("title"))
        url = text(item.get("url"))
        one_sentence = text(item.get("one_sentence"))
        paper = f"[{title}]({url})" if url else title
        lines.append(f"{idx}. **{paper}**：{one_sentence}")
    return lines


def render_run_info(candidates: dict[str, Any]) -> list[str]:
    fields = [
        ("raw_count", "原始抓取"),
        ("recent_count", "近期论文"),
        ("seen_count", "已处理过滤"),
        ("hard_excluded_count", "硬过滤"),
        ("category_policy_excluded_count", "类目策略过滤"),
        ("candidate_count", "候选数"),
        ("embedding_provider", "embedding provider"),
        ("embedding_model", "embedding model"),
    ]
    body = []
    for key, label in fields:
        if key in candidates:
            body.append(f"- {label}：{text(candidates.get(key))}")
    skipped = candidates.get("skipped_categories") or []
    if skipped:
        body.append(f"- 跳过类目：{text(skipped)}")
    return callout("info", "运行信息", body, folded=True)


def render_deep_dive(item: dict[str, Any], candidates: dict[str, dict[str, Any]]) -> str:
    cid = text(item.get("canonical_id"))
    meta = candidates.get(cid, {})
    title = text(item.get("title") or meta.get("title") or cid)
    url = text(item.get("url") or meta.get("url"))
    tags = item.get("tags") or meta.get("tags") or []
    lines = [
        f"### {title}",
        "",
        f"- canonical_id:: {cid}",
        f"- url:: {url}",
        f"- source_basis:: {text(item.get('source_basis') or 'title+abstract')}",
    ]
    if tags:
        lines.append(f"- tags:: {', '.join(str(t) for t in tags)}")
    lines.append("")
    lines.extend(callout("tip", "TL;DR", text(item.get("tldr"))))
    lines.append("")
    lines.extend(callout("note", "中文摘要", text(item.get("summary_zh"))))
    lines.append("")
    method_body = [
        "**研究问题**",
        text(item.get("problem")) or "无。",
        "",
        "**方法拆解**",
        text(item.get("method")) or "无。",
        "",
        "**核心创新**",
        *bullet_lines(item.get("core_innovations") or []),
    ]
    lines.extend(callout("example", "问题与方法", method_body))
    lines.append("")
    evidence_body = [
        "**实验与证据**",
        *bullet_lines(item.get("evidence") or []),
        "",
        "**局限性**",
        *bullet_lines(item.get("limitations") or []),
        "",
        "**待核查问题**",
        *bullet_lines(item.get("questions_to_check") or []),
    ]
    lines.extend(callout("warning", "证据、局限与待核查", evidence_body))
    lines.append("")
    idea_body = [
        "**与当前研究的关系**",
        text(item.get("relevance_to_me")) or "无。",
        "",
        "**可复用 idea**",
        *bullet_lines(item.get("reusable_ideas") or []),
        "",
        "**阅读优先级**",
        text(item.get("reading_priority")) or "无。",
    ]
    lines.extend(callout("abstract", "对我的价值", idea_body))
    return "\n".join(lines).strip() + "\n"


def render_item(item: dict[str, Any], candidates: dict[str, dict[str, Any]], section: str) -> str:
    cid = text(item.get("canonical_id"))
    meta = candidates.get(cid, {})
    title = text(item.get("title") or meta.get("title") or cid)
    url = text(item.get("url") or meta.get("url"))
    source = text(meta.get("source") or "arxiv")
    decision = text(item.get("decision") or SECTION_TITLES.get(section, section))
    tags = item.get("tags") or []
    lines = [
        f"### {title}",
        "",
        f"- source:: {source}",
        f"- canonical_id:: {cid}",
        f"- url:: {url}",
        f"- decision:: {decision}",
        "- action:: pending",
        "- feedback:: pending",
    ]
    if tags:
        lines.append(f"- tags:: {', '.join(str(t) for t in tags)}")
    lines.append("")
    lines.extend(callout(SECTION_CALLOUTS.get(section, "note"), "一句话结论", text(item.get("one_sentence"))))
    lines.append("")
    lines.extend(callout("note", "推荐理由", text(item.get("reason"))))
    lines.append("")
    lines.extend(callout("abstract", "与当前研究的关系", text(item.get("relevance"))))
    reusable = text(item.get("reusable_idea"))
    if reusable:
        lines.append("")
        lines.extend(callout("example", "可复用 idea", reusable, folded=True))
    uncertainty = text(item.get("uncertainty"))
    if uncertainty:
        lines.append("")
        lines.extend(callout("warning", "不确定点", uncertainty, folded=True))
    action = text(item.get("suggested_action"))
    if action:
        lines.append("")
        lines.extend(callout("todo", "建议动作", action))
    return "\n".join(lines).strip() + "\n"


def render_note(review: dict[str, Any], candidates: dict[str, Any], date: str) -> str:
    cmap = candidate_map(candidates)
    lines = [
        "---",
        f"date: {date}",
        "type: paper-daily",
        "source: arxiv",
        "runner: codex",
        "tags:",
        "  - paper-daily",
        "  - PaperPilot",
        "---",
        "",
        f"# {date} Paper Daily",
        "",
        "## 总览",
        "",
    ]
    lines.extend(callout("abstract", "今日结论", text(review.get("summary"))))
    lines.append("")
    lines.extend(render_run_info(candidates))
    lines.extend(
        [
            "",
            "## 快速导航",
            "",
            *render_quick_nav(review),
            "",
            "## 先读这几篇",
            "",
            *render_read_first(review),
            "",
        ]
    )
    deep_dives = review.get("deep_dives") or []
    if deep_dives:
        lines.extend(["## 重点论文深读", ""])
        for item in deep_dives:
            lines.append(render_deep_dive(item, cmap))
    lines.extend(
        [
            "## 分档推荐",
            "",
        ]
    )
    for key, heading in SECTION_TITLES.items():
        lines.extend([f"## {heading}", ""])
        items = review.get(key) or []
        if not items:
            lines.extend(["无。", ""])
            continue
        for item in items:
            lines.append(render_item(item, cmap, key))
    trends = review.get("trend_observations") or []
    lines.extend(["## 今日趋势", ""])
    if trends:
        lines.extend(callout("summary", "趋势观察", bullet_lines(trends)))
    else:
        lines.append("无。")
    lines.append("")
    followups = review.get("tomorrow_followups") or []
    lines.extend(["## 明日跟进", ""])
    if followups:
        lines.extend(callout("todo", "下一步", bullet_lines(followups)))
    else:
        lines.append("无。")
    lines.append("")
    return "\n".join(lines)


def update_seen(review: dict[str, Any], candidates: dict[str, Any], seen_path: Path) -> None:
    seen_path.parent.mkdir(parents=True, exist_ok=True)
    existing = set()
    if seen_path.exists():
        existing = {line.strip() for line in seen_path.read_text(encoding="utf-8").splitlines() if line.strip()}
    for paper in candidates.get("candidates", []) or []:
        cid = text(paper.get("canonical_id"))
        if cid:
            existing.add(cid)
    for key in SECTION_TITLES:
        for item in review.get(key) or []:
            cid = text(item.get("canonical_id"))
            if cid:
                existing.add(cid)
    seen_path.write_text("\n".join(sorted(existing)) + ("\n" if existing else ""), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="")
    parser.add_argument("--review", default="")
    parser.add_argument("--candidates", default="")
    parser.add_argument("--schema", default="config/review.schema.json")
    parser.add_argument("--output-dir", default="/Users/hym/Library/CloudStorage/OneDrive-std.uestc.edu.cn/Obsidian/3-PaperFlow")
    parser.add_argument("--seen-path", default="state/seen-papers.txt")
    args = parser.parse_args()

    date = args.date or dt.date.today().isoformat()
    review_path = Path(args.review or f"runs/{date}/review.json")
    candidates_path = Path(args.candidates or f"runs/{date}/candidates.json")
    review = load_json(review_path)
    candidates = load_json(candidates_path)
    validate_review(review, Path(args.schema))
    note = render_note(review, candidates, date)
    out_path = Path(args.output_dir) / "Paper-Daily" / date[:4] / f"{date}-paper-codex.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(note, encoding="utf-8")
    update_seen(review, candidates, Path(args.seen_path))
    print(f"[paperpilot] wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
