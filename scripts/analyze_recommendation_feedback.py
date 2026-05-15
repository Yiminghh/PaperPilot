#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import datetime as dt
from pathlib import Path
from typing import Any

from config_utils import DEFAULT_CONFIG, load_config, resolve_path
from paperpilot_utils import clean_text, load_jsonl


POSITIVE_ACTIONS = {"read", "save"}
NEGATIVE_ACTIONS = {"skip", "not_relevant"}


def counter_table(counter: collections.Counter[str], empty: str = "无。") -> list[str]:
    if not counter:
        return [empty]
    lines = ["| 项 | 次数 |", "|---|---:|"]
    for key, value in counter.most_common(12):
        lines.append(f"| {key or '(empty)'} | {value} |")
    return lines


def action_counter(records: list[dict[str, Any]]) -> collections.Counter[str]:
    return collections.Counter(clean_text(r.get("user_action")) for r in records)


def tag_counter(records: list[dict[str, Any]]) -> collections.Counter[str]:
    counter: collections.Counter[str] = collections.Counter()
    for rec in records:
        for tag in rec.get("tags") or []:
            if clean_text(tag):
                counter[clean_text(tag)] += 1
    return counter


def title_patterns(records: list[dict[str, Any]]) -> collections.Counter[str]:
    counter: collections.Counter[str] = collections.Counter()
    for rec in records:
        title = clean_text(rec.get("title"))
        for token in title.replace(":", " ").replace("-", " ").split():
            token = token.strip(",.()[]").lower()
            if len(token) >= 6:
                counter[token] += 1
    return counter


def recommendation_precision(records: list[dict[str, Any]]) -> tuple[int, int]:
    must = [r for r in records if clean_text(r.get("system_decision")) == "必读"]
    positive = [r for r in must if clean_text(r.get("user_action")) in POSITIVE_ACTIONS]
    return len(positive), len(must)


def numeric_ratings(records: list[dict[str, Any]]) -> list[float]:
    ratings = []
    for record in records:
        value = clean_text(record.get("user_rating"))
        if not value or value == "pending":
            continue
        try:
            ratings.append(float(value))
        except ValueError:
            continue
    return ratings


def record_date(record: dict[str, Any]) -> dt.date | None:
    try:
        return dt.date.fromisoformat(clean_text(record.get("date")))
    except ValueError:
        return None


def filter_records(records: list[dict[str, Any]], date: dt.date, scope: str) -> list[dict[str, Any]]:
    if scope == "all":
        return records
    target_year, target_week, _ = date.isocalendar()
    out = []
    for record in records:
        rec_date = record_date(record)
        if not rec_date:
            continue
        year, week, _ = rec_date.isocalendar()
        if (year, week) == (target_year, target_week):
            out.append(record)
    return out


def evidence_examples(records: list[dict[str, Any]], tag: str, limit: int = 2) -> str:
    matches = []
    for record in records:
        if tag in {clean_text(t) for t in record.get("tags") or []}:
            feedback = clean_text(record.get("user_feedback"))
            suffix = f": {feedback}" if feedback and feedback != "pending" else ""
            matches.append(f"{clean_text(record.get('date'))} {clean_text(record.get('canonical_id'))}{suffix}")
        if len(matches) >= limit:
            break
    return "; ".join(matches)


def build_report(records: list[dict[str, Any]], total_records: int, min_feedback: int, date: dt.date, scope: str) -> str:
    year, week, _ = date.isocalendar()
    positives = [r for r in records if clean_text(r.get("user_action")) in POSITIVE_ACTIONS]
    negatives = [r for r in records if clean_text(r.get("user_action")) in NEGATIVE_ACTIONS]
    ratings = numeric_ratings(records)
    pos, total_must = recommendation_precision(records)
    lines = [
        "---",
        f"date: {date.isoformat()}",
        "type: paperpilot-feedback-report",
        f"week: {year}-W{week:02d}",
        "---",
        "",
        f"# Weekly Recommendation Drift Report {year}-W{week:02d}",
        "",
        "## Summary",
        "",
        f"- Feedback records: {len(records)}",
        f"- Scope: {scope}",
        f"- Total historical feedback records: {total_records}",
        f"- Positive actions (`read/save`): {len(positives)}",
        f"- Negative actions (`skip/not_relevant`): {len(negatives)}",
        f"- Average rating: {sum(ratings) / len(ratings):.2f}" if ratings else "- Average rating: no numeric ratings yet",
        f"- Must-read positive rate: {pos}/{total_must}" if total_must else "- Must-read positive rate: no labeled must-read records yet",
        "",
    ]
    if len(records) < min_feedback:
        lines.extend(
            [
                "> [!warning] 数据不足",
                f"> 当前只有 {len(records)} 条反馈，少于 {min_feedback} 条。只记录观察，不建议改配置。",
                "",
            ]
        )
    lines.extend(["## Action Distribution", "", *counter_table(action_counter(records)), ""])
    lines.extend(["## Positive Signals", ""])
    lines.extend(counter_table(tag_counter(positives), empty="暂无 read/save 标签。"))
    lines.append("")
    lines.extend(["## Negative Signals", ""])
    lines.extend(counter_table(tag_counter(negatives), empty="暂无 skip/not_relevant 标签。"))
    lines.append("")
    lines.extend(["## Repeated Negative Title Tokens", ""])
    lines.extend(counter_table(title_patterns(negatives), empty="暂无可分析标题模式。"))
    lines.append("")
    lines.extend(["## Proposed Config Changes", ""])
    if len(records) < min_feedback:
        lines.append("- 暂不建议修改配置；继续积累反馈。")
    else:
        negative_tags = tag_counter(negatives)
        positive_tags = tag_counter(positives)
        proposals = []
        for tag, count in negative_tags.most_common(5):
            if count >= 2 and count > positive_tags.get(tag, 0):
                examples = evidence_examples(negatives, tag)
                proposals.append(
                    f"- 考虑降低 `{tag}` 权重或加入更细的 category policy。"
                    f"证据：负反馈 {count} 次；例：{examples or '无具体例子'}。"
                )
        for tag, count in positive_tags.most_common(5):
            if count >= 2 and count > negative_tags.get(tag, 0):
                examples = evidence_examples(positives, tag)
                proposals.append(
                    f"- 考虑提高 `{tag}` 权重。证据：正反馈 {count} 次；例：{examples or '无具体例子'}。"
                )
        lines.extend(proposals or ["- 暂无足够明确的调参信号。"])
    lines.extend(
        [
            "",
            "## Changes Not Applied",
            "",
            "- 本报告只提出建议，不自动修改 `config/interest-profile.yaml` 或 `config/paper-daily-config.yaml`。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--feedback", default="")
    parser.add_argument("--output-root", default="")
    parser.add_argument("--date", default=dt.date.today().isoformat())
    parser.add_argument("--min-feedback", type=int, default=10)
    parser.add_argument("--scope", choices=["week", "all"], default="week")
    args = parser.parse_args()

    config = load_config(args.config)
    date = dt.date.fromisoformat(args.date)
    year, week, _ = date.isocalendar()
    feedback_path = Path(args.feedback).expanduser() if args.feedback else resolve_path(
        config, "paths.feedback_path", "state/paper-feedback.jsonl"
    )
    output_root = Path(args.output_root).expanduser() if args.output_root else resolve_path(
        config, "paths.weekly_runs_dir", "runs/weekly"
    )
    out_dir = output_root / f"{year}-W{week:02d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "recommendation-drift-report.md"
    all_records = load_jsonl(feedback_path)
    records = filter_records(all_records, date, args.scope)
    out_path.write_text(build_report(records, len(all_records), args.min_feedback, date, args.scope), encoding="utf-8")
    print(f"[paperpilot] wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
