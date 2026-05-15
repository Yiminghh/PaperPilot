#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import io
import re
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .config_utils import DEFAULT_CONFIG, load_config, run_dir
from .paperpilot_utils import candidate_map, canonical_to_arxiv_id, clean_text, load_json, write_json


SECTION_ALIASES = {
    "abstract": ("abstract",),
    "introduction": ("introduction", "background"),
    "methods": ("method", "methods", "approach", "model", "framework"),
    "experiments": ("experiment", "experiments", "evaluation", "results", "benchmark"),
    "limitations": ("limitation", "limitations", "discussion", "conclusion"),
}

class SimplePaperHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.current_tag = ""
        self.buffer: list[str] = []
        self.blocks: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"h1", "h2", "h3", "p", "li"}:
            self.flush()
            self.current_tag = tag

    def handle_endtag(self, tag: str) -> None:
        if tag == self.current_tag:
            self.flush()
            self.current_tag = ""

    def handle_data(self, data: str) -> None:
        if self.current_tag:
            self.buffer.append(data)

    def flush(self) -> None:
        if not self.current_tag or not self.buffer:
            self.buffer = []
            return
        text = clean_text(" ".join(self.buffer))
        if text:
            self.blocks.append((self.current_tag, text))
        self.buffer = []


def fetch_url(url: str, timeout: int) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "paperpilot/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_bytes(url: str, timeout: int) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "paperpilot/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def section_key(heading: str) -> str:
    normalized = heading.lower()
    normalized = re.sub(r"[^a-z0-9 ]+", " ", normalized)
    for key, aliases in SECTION_ALIASES.items():
        if any(alias in normalized for alias in aliases):
            return key
    return ""


def parse_html_sections(raw_html: str) -> dict[str, str]:
    parser = SimplePaperHTMLParser()
    parser.feed(raw_html)
    parser.flush()
    sections: dict[str, list[str]] = {key: [] for key in SECTION_ALIASES}
    current = ""
    for tag, value in parser.blocks:
        if tag in {"h1", "h2", "h3"}:
            current = section_key(value)
            continue
        if current and tag in {"p", "li"}:
            sections[current].append(value)
    return {key: trim_section(" ".join(values)) for key, values in sections.items() if values}


def trim_section(value: str, max_chars: int = 3500) -> str:
    value = clean_text(value)
    if len(value) <= max_chars:
        return value
    return value[:max_chars].rsplit(" ", 1)[0] + " ..."


def extract_pdf_text(pdf_bytes: bytes, max_pages: int) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("Missing dependency: pypdf. Install it to enable PDF fallback.") from exc
    reader = PdfReader(io.BytesIO(pdf_bytes))
    parts = []
    for page in reader.pages[:max(max_pages, 1)]:
        parts.append(page.extract_text() or "")
    return "\n".join(parts)


def parse_pdf_sections(raw_text: str) -> dict[str, str]:
    lines = [clean_text(line) for line in raw_text.splitlines()]
    lines = [line for line in lines if line]
    sections: dict[str, list[str]] = {key: [] for key in SECTION_ALIASES}
    current = ""
    for line in lines:
        key = section_key(line)
        if key and len(line) <= 90:
            current = key
            continue
        if current:
            sections[current].append(line)
    parsed = {key: trim_section(" ".join(values), max_chars=4500) for key, values in sections.items() if values}
    if not parsed and raw_text.strip():
        parsed["pdf_excerpt"] = trim_section(raw_text, max_chars=12000)
    return parsed


def select_top_ids(review: dict[str, Any], top_n: int) -> list[str]:
    ids = []
    for item in review.get("must_read") or []:
        cid = item.get("canonical_id")
        if cid:
            ids.append(cid)
        if len(ids) >= top_n:
            break
    return ids


def enrich_one(
    paper: dict[str, Any],
    timeout: int,
    offline: bool,
    pdf_fallback: bool,
    max_pdf_pages: int,
) -> dict[str, Any]:
    cid = paper.get("canonical_id", "")
    arxiv_id = canonical_to_arxiv_id(cid)
    sections = {"abstract": trim_section(paper.get("abstract", ""))}
    sources_tried: list[str] = []
    fetch_status = "abstract_only" if offline else "fallback_abstract_only"
    errors: list[str] = []
    used_sources: set[str] = set()
    if not offline and arxiv_id:
        html_urls = (f"https://arxiv.org/html/{arxiv_id}", f"https://ar5iv.labs.arxiv.org/html/{arxiv_id}")
        for index, url in enumerate(html_urls):
            sources_tried.append(url)
            try:
                raw_html = fetch_url(url, timeout=timeout)
                parsed = parse_html_sections(raw_html)
                if parsed:
                    sections.update(parsed)
                    used_sources.add("html")
                    fetch_status = "ok" if len(parsed) > 1 else "partial"
                    break
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                errors.append(f"{url}: {type(exc).__name__}: {exc}")
            if index < len(html_urls) - 1:
                time.sleep(1)
        if pdf_fallback and "html" not in used_sources:
            pdf_url = paper.get("pdf_url") or f"https://arxiv.org/pdf/{arxiv_id}"
            sources_tried.append(pdf_url)
            try:
                pdf_text = extract_pdf_text(fetch_bytes(pdf_url, timeout=timeout), max_pages=max_pdf_pages)
                parsed_pdf = parse_pdf_sections(pdf_text)
                if parsed_pdf:
                    sections.update(parsed_pdf)
                    used_sources.add("pdf")
                    fetch_status = "ok" if len(parsed_pdf) > 1 else "partial"
            except (urllib.error.URLError, TimeoutError, OSError, RuntimeError) as exc:
                errors.append(f"{pdf_url}: {type(exc).__name__}: {exc}")
    if used_sources == {"html", "pdf"}:
        source_basis = "html+pdf"
    elif "pdf" in used_sources:
        source_basis = "pdf"
    elif "html" in used_sources:
        source_basis = "html"
    else:
        source_basis = "title+abstract"
    return {
        "canonical_id": cid,
        "title": paper.get("title", ""),
        "url": paper.get("url", ""),
        "pdf_url": paper.get("pdf_url", ""),
        "source_basis": source_basis,
        "fetch_status": fetch_status,
        "sources_tried": sources_tried,
        "errors": errors,
        "sections": sections,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--date", default=dt.date.today().isoformat())
    parser.add_argument("--review", default="")
    parser.add_argument("--candidates", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--top-n", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--max-pdf-pages", type=int, default=12)
    parser.add_argument("--no-pdf-fallback", action="store_true")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    today_run_dir = run_dir(config, args.date)
    review_path = Path(args.review).expanduser() if args.review else today_run_dir / "review.json"
    candidates_path = Path(args.candidates).expanduser() if args.candidates else today_run_dir / "candidates.json"
    output_path = Path(args.output).expanduser() if args.output else today_run_dir / "enriched.json"
    review = load_json(review_path)
    candidates = candidate_map(load_json(candidates_path))
    selected = select_top_ids(review, max(args.top_n, 0))
    papers = []
    for cid in selected:
        paper = candidates.get(cid)
        if not paper:
            continue
        papers.append(
            enrich_one(
                paper,
                timeout=max(args.timeout, 5),
                offline=args.offline,
                pdf_fallback=not args.no_pdf_fallback,
                max_pdf_pages=args.max_pdf_pages,
            )
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": args.date,
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "top_n": args.top_n,
        "papers": papers,
    }
    write_json(output_path, payload)
    print(f"[paperpilot] wrote {output_path} ({len(papers)} papers)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
