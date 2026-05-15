#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path
from typing import Any

from .config_utils import DEFAULT_CONFIG, load_config, project_root, resolve_path, run_dir
from .paperpilot_utils import candidate_map, clean_text as text, load_json
from .sync_feedback_from_notes import parse_note


SECTION_TITLES = {
    "must_read": "今日必读",
    "worth_reading": "值得看",
    "later": "稍后",
    "skip": "跳过",
}


def validate_review(review: dict[str, Any], schema_path: Path) -> None:
    schema = load_required_json(schema_path, "review schema")
    try:
        import jsonschema
    except ImportError:
        missing = [k for k in schema.get("required", []) if k not in review]
        if missing:
            raise SystemExit(f"review.json missing required keys: {missing}")
        return
    jsonschema.validate(review, schema)


def load_required_json(path: Path, label: str) -> Any:
    if not path.exists():
        raise SystemExit(f"{label} not found: {path}")
    return load_json(path)


def verification_status(paper: dict[str, Any]) -> str:
    verification = paper.get("verification") or {}
    status = text(verification.get("status"))
    if status:
        return status
    cid = text(paper.get("canonical_id"))
    if paper.get("source") == "arxiv" and cid.startswith("arxiv:"):
        return "verified"
    return "unverified"


def validate_review_against_candidates(review: dict[str, Any], candidates: dict[str, Any]) -> None:
    cmap = candidate_map(candidates)
    errors = []
    seen_review_ids: set[str] = set()
    must_ids = {text(item.get("canonical_id")) for item in review.get("must_read") or []}
    for section in SECTION_TITLES:
        for item in review.get(section) or []:
            cid = text(item.get("canonical_id"))
            if not cid:
                errors.append(f"{section}: missing canonical_id")
                continue
            if cid in seen_review_ids:
                errors.append(f"{cid}: appears in multiple recommendation sections")
            seen_review_ids.add(cid)
            paper = cmap.get(cid)
            if not paper:
                errors.append(f"{cid}: review item is not present in candidates.json")
                continue
            status = verification_status(paper)
            if status == "conflict":
                errors.append(f"{cid}: verification conflict; do not render conflicted papers")
            if section == "must_read" and status != "verified":
                errors.append(f"{cid}: must_read requires verification.status=verified, got {status}")
    seen_deep_ids: set[str] = set()
    for item in review.get("deep_dives") or []:
        cid = text(item.get("canonical_id"))
        if not cid:
            errors.append("deep_dives: missing canonical_id")
            continue
        if cid in seen_deep_ids:
            errors.append(f"{cid}: duplicate deep_dive")
        seen_deep_ids.add(cid)
        if cid and cid not in must_ids:
            errors.append(f"{cid}: deep_dive must correspond to a must_read paper")
    if errors:
        raise SystemExit("review/candidates validation failed:\n- " + "\n- ".join(errors))


def bullet_lines(values: list[Any]) -> list[str]:
    cleaned = [text(v) for v in values if text(v)]
    if not cleaned:
        return ["- 无。"]
    return [f"- {value}" for value in cleaned]


def list_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [text(item) for item in value if text(item)]
    cleaned = text(value)
    return [cleaned] if cleaned else []


def format_tags(value: Any) -> str:
    return ", ".join(list_values(value))


def paper_view(item: dict[str, Any], candidates: dict[str, dict[str, Any]]) -> dict[str, Any]:
    cid = text(item.get("canonical_id"))
    meta = candidates.get(cid, {})
    return {
        "cid": cid,
        "title": text(item.get("title") or meta.get("title") or cid),
        "url": text(item.get("url") or meta.get("url")),
        "tags": item.get("tags") or meta.get("tags") or [],
        "meta": meta,
    }


def core_callout(body: list[str]) -> list[str]:
    lines = ["> [!tip] 核心内容"]
    for line in body or ["无。"]:
        lines.append(f"> {line}" if line else ">")
    return lines


def add_field(lines: list[str], heading: str, body: Any) -> None:
    lines.extend(["", f"**{heading}**"])
    if isinstance(body, list):
        lines.extend(body or ["- 无。"])
    else:
        lines.append(text(body) or "无。")


def existing_feedback_map(note_path: Path) -> dict[str, dict[str, Any]]:
    if not note_path.exists():
        return {}
    return {rec["canonical_id"]: rec for rec in parse_note(note_path, include_pending=False)}


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
    return body or ["- 无。"]


def render_deep_dive(item: dict[str, Any], candidates: dict[str, dict[str, Any]], index: int) -> str:
    paper = paper_view(item, candidates)
    lines = [
        f"### {index}. {paper['title']}",
        "",
        f"- canonical_id:: {paper['cid']}",
        f"- url:: {paper['url']}",
        f"- source_basis:: {text(item.get('source_basis') or 'title+abstract')}",
    ]
    tags = format_tags(paper["tags"])
    if tags:
        lines.append(f"- tags:: {tags}")
    lines.append("")
    lines.extend(
        core_callout(
            [
                f"**TL;DR**：{text(item.get('tldr')) or '无。'}",
                "",
                f"**对我的价值**：{text(item.get('relevance_to_me')) or '无。'}",
                "",
                f"**阅读优先级**：{text(item.get('reading_priority')) or '无。'}",
            ]
        )
    )
    add_field(lines, "中文摘要", item.get("summary_zh"))
    add_field(lines, "研究问题", item.get("problem"))
    add_field(lines, "方法拆解", item.get("method"))
    add_field(lines, "核心创新", bullet_lines(item.get("core_innovations") or []))
    add_field(lines, "实验与证据", bullet_lines(item.get("evidence") or []))
    add_field(lines, "局限性", bullet_lines(item.get("limitations") or []))
    add_field(lines, "可复用 idea", bullet_lines(item.get("reusable_ideas") or []))
    add_field(lines, "待核查问题", bullet_lines(item.get("questions_to_check") or []))
    return "\n".join(lines).strip() + "\n"


def render_item(
    item: dict[str, Any],
    candidates: dict[str, dict[str, Any]],
    section: str,
    existing_feedback: dict[str, dict[str, Any]],
    index: int,
) -> str:
    paper = paper_view(item, candidates)
    meta = paper["meta"]
    feedback = existing_feedback.get(paper["cid"], {})
    action_value = text(feedback.get("user_action") or "pending")
    rating_value = text(feedback.get("user_rating") or "pending")
    feedback_value = text(feedback.get("user_feedback") or "pending")
    source = text(meta.get("source") or "arxiv")
    decision = text(item.get("decision") or SECTION_TITLES.get(section, section))
    lines = [
        f"### {index}. {paper['title']}",
        "",
        f"- source:: {source}",
        f"- canonical_id:: {paper['cid']}",
        f"- url:: {paper['url']}",
        f"- decision:: {decision}",
        f"- action:: {action_value}",
        f"- rating:: {rating_value}",
        f"- feedback:: {feedback_value}",
    ]
    tags = format_tags(paper["tags"])
    if tags:
        lines.append(f"- tags:: {tags}")
    add_field(lines, "一句话结论", item.get("one_sentence"))
    add_field(lines, "推荐理由", item.get("reason"))
    add_field(lines, "与当前研究的关系", item.get("relevance"))
    reusable = text(item.get("reusable_idea"))
    if reusable:
        add_field(lines, "可复用 idea", reusable)
    uncertainty = text(item.get("uncertainty"))
    if uncertainty:
        add_field(lines, "不确定点", uncertainty)
    action = text(item.get("suggested_action"))
    if action:
        add_field(lines, "建议动作", action)
    return "\n".join(lines).strip() + "\n"


def render_overview(review: dict[str, Any], candidates: dict[str, Any], date: str) -> list[str]:
    return [
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
        text(review.get("summary")) or "无。",
        "",
        "### 运行信息",
        "",
        *render_run_info(candidates),
        "",
    ]


def render_deep_dives(review: dict[str, Any], cmap: dict[str, dict[str, Any]]) -> list[str]:
    items = review.get("deep_dives") or []
    if not items:
        return []
    lines = ["## 重点论文深读", ""]
    for index, item in enumerate(items, start=1):
        lines.append(render_deep_dive(item, cmap, index))
    return lines


def render_ranked_sections(
    review: dict[str, Any],
    cmap: dict[str, dict[str, Any]],
    existing_feedback: dict[str, dict[str, Any]],
) -> list[str]:
    lines = ["## 分档推荐", ""]
    for key, heading in SECTION_TITLES.items():
        lines.extend([f"## {heading}", ""])
        items = review.get(key) or []
        if not items:
            lines.extend(["无。", ""])
            continue
        for index, item in enumerate(items, start=1):
            lines.append(render_item(item, cmap, key, existing_feedback, index))
    return lines


def render_list_section(title: str, values: list[Any]) -> list[str]:
    lines = [f"## {title}", ""]
    lines.extend(bullet_lines(values) if values else ["无。"])
    lines.append("")
    return lines


def render_note(
    review: dict[str, Any],
    candidates: dict[str, Any],
    date: str,
    existing_feedback: dict[str, dict[str, Any]] | None = None,
) -> str:
    cmap = candidate_map(candidates)
    lines = render_overview(review, candidates, date)
    lines.extend(render_deep_dives(review, cmap))
    lines.extend(
        render_ranked_sections(
            review,
            cmap,
            existing_feedback or {},
        )
    )
    lines.extend(render_list_section("今日趋势", review.get("trend_observations") or []))
    lines.extend(render_list_section("明日跟进", review.get("tomorrow_followups") or []))
    return "\n".join(lines)


def reviewed_ids(review: dict[str, Any]) -> set[str]:
    seen = set()
    for key in SECTION_TITLES:
        for item in review.get(key) or []:
            cid = text(item.get("canonical_id"))
            if cid:
                seen.add(cid)
    for item in review.get("deep_dives") or []:
        cid = text(item.get("canonical_id"))
        if cid:
            seen.add(cid)
    return seen


def update_seen(review: dict[str, Any], seen_path: Path) -> None:
    seen_path.parent.mkdir(parents=True, exist_ok=True)
    existing = set()
    if seen_path.exists():
        existing = {line.strip() for line in seen_path.read_text(encoding="utf-8").splitlines() if line.strip()}
    existing.update(reviewed_ids(review))
    seen_path.write_text("\n".join(sorted(existing)) + ("\n" if existing else ""), encoding="utf-8")


def note_path(config: dict[str, Any], date: str, output_dir: str = "") -> Path:
    root = Path(output_dir).expanduser() if output_dir else resolve_path(config, "paths.paperflow_root")
    return root / "Paper-Daily" / date[:4] / f"{date}-paper-codex.md"


def schema_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else project_root() / path


def write_daily_note(
    review_path: Path,
    candidates_path: Path,
    schema: Path,
    out_path: Path,
    seen_path: Path,
    date: str,
) -> dict[str, Any]:
    review = load_required_json(review_path, "review")
    candidates = load_required_json(candidates_path, "candidates")
    validate_review(review, schema)
    validate_review_against_candidates(review, candidates)
    note = render_note(review, candidates, date, existing_feedback=existing_feedback_map(out_path))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(note, encoding="utf-8")
    update_seen(review, seen_path)
    return review


def update_memory_after_render(
    review_path: Path,
    candidates_path: Path,
    feedback_path: Path,
    memory_root: Path,
    date: str,
    strict: bool,
) -> None:
    try:
        from .update_paper_memory import update_memory

        count = update_memory(
            review_path=review_path,
            candidates_path=candidates_path,
            feedback_path=feedback_path,
            memory_root=memory_root,
            date=date,
            max_query_chars=8000,
        )
        print(f"[paperpilot] updated paper memory: {count} papers")
    except Exception as exc:
        if strict:
            raise
        print(f"[paperpilot][warn] paper memory update skipped: {type(exc).__name__}: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--date", default="")
    parser.add_argument("--review", default="")
    parser.add_argument("--candidates", default="")
    parser.add_argument("--schema", default="config/review.schema.json")
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--seen-path", default="")
    parser.add_argument("--feedback-path", default="")
    parser.add_argument("--memory-root", default="")
    parser.add_argument("--skip-memory-update", action="store_true")
    parser.add_argument("--strict-memory-update", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    date = args.date or dt.date.today().isoformat()
    today_run_dir = run_dir(config, date)
    review_path = Path(args.review).expanduser() if args.review else today_run_dir / "review.json"
    candidates_path = Path(args.candidates).expanduser() if args.candidates else today_run_dir / "candidates.json"
    seen_path = Path(args.seen_path).expanduser() if args.seen_path else resolve_path(
        config, "paths.seen_path", "state/seen-papers.txt"
    )
    feedback_path = Path(args.feedback_path).expanduser() if args.feedback_path else resolve_path(
        config, "paths.feedback_path", "state/paper-feedback.jsonl"
    )
    memory_root = Path(args.memory_root).expanduser() if args.memory_root else resolve_path(
        config, "paths.memory_dir", "state/paper-memory"
    )
    out_path = note_path(config, date, args.output_dir)
    write_daily_note(
        review_path=review_path,
        candidates_path=candidates_path,
        schema=schema_path(args.schema),
        out_path=out_path,
        seen_path=seen_path,
        date=date,
    )
    if not args.skip_memory_update:
        update_memory_after_render(
            review_path=review_path,
            candidates_path=candidates_path,
            feedback_path=feedback_path,
            memory_root=memory_root,
            date=date,
            strict=args.strict_memory_update,
        )
    print(f"[paperpilot] wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
