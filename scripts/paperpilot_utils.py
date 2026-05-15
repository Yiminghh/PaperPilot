from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(x) for x in value)
    return re.sub(r"\s+", " ", str(value)).strip()


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return {} if default is None else default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in records)
        + ("\n" if records else ""),
        encoding="utf-8",
    )


def normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


def strip_arxiv_version(arxiv_id: str) -> str:
    return re.sub(r"v\d+$", "", arxiv_id.rstrip("/").split("/")[-1])


def canonical_arxiv_id(source_id: str) -> str:
    return f"arxiv:{strip_arxiv_version(source_id)}"


def canonical_to_arxiv_id(canonical_id: str) -> str:
    if canonical_id.startswith("arxiv:"):
        return strip_arxiv_version(canonical_id.split(":", 1)[1])
    return strip_arxiv_version(canonical_id)


def candidate_map(candidates: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(p.get("canonical_id", "")): p
        for p in candidates.get("candidates", []) or candidates.get("papers", []) or []
        if p.get("canonical_id")
    }


def project_path_arg(value: str | Path, root: Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return root / path


def yaml_quote(value: Any) -> str:
    text = clean_text(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'
