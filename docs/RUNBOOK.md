---
title: PaperPilot 运行手册
updated: 2026-05-15
tags:
  - PaperPilot
---

# PaperPilot 运行手册

本文记录日常运行步骤——即「怎么用」。架构与设计理由见 [DESIGN.md](DESIGN.md)。

## 每日流程

```text
scripts/run_candidate_pipeline.sh
  -> runs/YYYY-MM-DD/candidates.json
Codex 写入:
  -> runs/YYYY-MM-DD/review.json
python scripts/render_daily_note.py --date YYYY-MM-DD
  -> Paper-Daily/YYYY/YYYY-MM-DD-paper-codex.md
```

`run_candidate_pipeline.sh` 负责确定性的前置流程：反馈同步 → arXiv 抓取 → BM25/embedding 检索 → 论文校验 → `candidates.json`。

`review.json` 是 Codex 每日唯一应写的文件。Markdown 日报由 `paperpilot.render_daily_note` 渲染，不手写。

Top 论文加厚（可选）：在写 `deep_dives` 前运行 `python scripts/enrich_top_papers.py --date YYYY-MM-DD --top-n 3`，优先 HTML，失败回退 Top 论文 PDF。

## 配置

tracked 默认值在以下文件（各自职责见 [DESIGN.md](DESIGN.md) 的「配置边界」）：

```text
config/paper-daily-config.yaml
config/interest-profile.yaml
config/negative-keywords.yaml
```

机器相关路径放在 gitignored 的 `config/local.yaml`，从模板复制：

```text
config/local.example.yaml
```

`config/local.yaml` 会 deep-merge 覆盖 `config/paper-daily-config.yaml`。

## 目录结构

```text
src/paperpilot/    # 实现代码
scripts/           # 薄 CLI wrapper
config/            # 运行配置与 prompt
tests/             # 回归检查
```

可复用逻辑放 `src/paperpilot/`，`scripts/` 保持薄。

## 检查

```bash
scripts/check.sh
```

检查脚本默认用 `./.venv/bin/python`，并把 `src/` 加入 `PYTHONPATH`。覆盖方式：

```bash
PAPERPILOT_PYTHON=/path/to/python scripts/check.sh
```

## 反馈契约

每日日报里每篇论文块暴露：

```text
- action:: pending
- rating:: pending
- feedback:: pending
```

`scripts/sync_feedback_from_notes.py` 把非 `pending` 的值写入 `state/paper-feedback.jsonl`。

合法 `action`：

```text
read | save | later | skip | not_relevant | pending
```

重新渲染同一天的日报时，已有的标注会从笔记中读回并保留，不会被刷成 `pending`。

## 已读去重

`state/seen-papers.txt` 从「出现在已渲染评审分档中的论文」更新。因此每日评审 prompt 要求 Codex 把每篇候选恰好归类一次到 `must_read` / `worth_reading` / `later` / `skip`。

## 运行产物

以下目录留在本地、不进版本控制、不进 Obsidian：

```text
.cache/
logs/
runs/
state/
```
