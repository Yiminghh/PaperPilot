#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from config_utils import DEFAULT_CONFIG, load_config, run_dir
from paperpilot_utils import canonical_to_arxiv_id, clean_text, load_json, normalize_title, write_json


ARXIV_API = "https://export.arxiv.org/api/query"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


def title_match(a: str, b: str) -> bool:
    na = normalize_title(a)
    nb = normalize_title(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    shorter, longer = sorted((na, nb), key=len)
    return len(shorter) > 20 and shorter in longer


def fetch_arxiv_batch(arxiv_ids: list[str], timeout: int) -> dict[str, dict[str, str]]:
    if not arxiv_ids:
        return {}
    params = urllib.parse.urlencode({"id_list": ",".join(arxiv_ids)})
    req = urllib.request.Request(f"{ARXIV_API}?{params}", headers={"User-Agent": "paperpilot/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        xml_data = resp.read()
    root = ET.fromstring(xml_data)
    out = {}
    for entry in root.findall("atom:entry", ATOM_NS):
        entry_id = canonical_to_arxiv_id(entry.findtext("atom:id", namespaces=ATOM_NS) or "")
        title = clean_text(entry.findtext("atom:title", namespaces=ATOM_NS))
        out[entry_id] = {"arxiv_id": entry_id, "title": title}
    return out


def verify_with_crossref(doi: str, title: str, timeout: int) -> dict[str, Any]:
    doi = doi.strip()
    if not doi:
        return {"status": "unverified", "via": "", "reason": "missing DOI"}
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="")
    req = urllib.request.Request(url, headers={"User-Agent": "paperpilot/0.1 (mailto:y.huang24@imperial.ac.uk)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        titles = data.get("message", {}).get("title") or []
        crossref_title = titles[0] if titles else ""
        if not title or title_match(title, crossref_title):
            return {"status": "verified", "via": "crossref", "matched_title": crossref_title}
        return {
            "status": "conflict",
            "via": "crossref",
            "matched_title": crossref_title,
            "reason": "DOI resolved but title differs",
        }
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {"status": "verify_pending", "via": "crossref", "reason": f"{type(exc).__name__}: {exc}"}


def verify_papers(payload: dict[str, Any], trust_arxiv_source: bool, timeout: int) -> tuple[list[dict[str, Any]], dict[str, int]]:
    papers = payload.get("candidates") or payload.get("papers") or []
    now = dt.datetime.now(dt.UTC).isoformat()
    arxiv_needs = []
    for paper in papers:
        cid = paper.get("canonical_id", "")
        if cid.startswith("arxiv:") and not (trust_arxiv_source and paper.get("source") == "arxiv"):
            arxiv_needs.append(canonical_to_arxiv_id(cid))
    arxiv_verified: dict[str, dict[str, str]] = {}
    arxiv_error = ""
    if arxiv_needs:
        try:
            for start in range(0, len(arxiv_needs), 50):
                arxiv_verified.update(fetch_arxiv_batch(arxiv_needs[start : start + 50], timeout=timeout))
        except (urllib.error.URLError, TimeoutError, OSError, ET.ParseError) as exc:
            arxiv_error = f"{type(exc).__name__}: {exc}"

    results = []
    summary = {"verified": 0, "unverified": 0, "verify_pending": 0, "conflict": 0}
    for paper in papers:
        cid = paper.get("canonical_id", "")
        title = paper.get("title", "")
        dedupe = paper.get("dedupe_keys") or {}
        verification: dict[str, Any]
        if trust_arxiv_source and paper.get("source") == "arxiv" and cid.startswith("arxiv:"):
            verification = {
                "status": "verified",
                "via": "arxiv-source",
                "checked_at": now,
                "reason": "paper was fetched directly from arXiv API",
            }
        elif cid.startswith("arxiv:"):
            aid = canonical_to_arxiv_id(cid)
            matched = arxiv_verified.get(aid)
            if matched and title_match(title, matched.get("title", "")):
                verification = {
                    "status": "verified",
                    "via": "arxiv",
                    "checked_at": now,
                    "matched_title": matched.get("title", ""),
                }
            elif matched:
                verification = {
                    "status": "conflict",
                    "via": "arxiv",
                    "checked_at": now,
                    "matched_title": matched.get("title", ""),
                    "reason": "arXiv ID resolved but title differs",
                }
            else:
                verification = {
                    "status": "verify_pending" if arxiv_error else "unverified",
                    "via": "arxiv",
                    "checked_at": now,
                    "reason": arxiv_error or "arXiv ID not found",
                }
        elif dedupe.get("doi"):
            verification = verify_with_crossref(str(dedupe.get("doi")), title, timeout=timeout)
            verification["checked_at"] = now
        else:
            verification = {
                "status": "unverified",
                "via": "",
                "checked_at": now,
                "reason": "no arXiv ID or DOI",
            }
        status = verification.get("status", "unverified")
        summary[status] = summary.get(status, 0) + 1
        results.append(
            {
                "canonical_id": cid,
                "title": title,
                "verification": verification,
            }
        )
    return results, summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--input", default="")
    parser.add_argument("--date", default=dt.date.today().isoformat())
    parser.add_argument("--output", default="")
    parser.add_argument("--write-back", action="store_true")
    parser.add_argument("--trust-arxiv-source", action="store_true")
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args()

    config = load_config(args.config)
    today_run_dir = run_dir(config, args.date)
    input_path = Path(args.input).expanduser() if args.input else today_run_dir / "candidates.json"
    output_path = Path(args.output).expanduser() if args.output else today_run_dir / "verification.json"
    payload = load_json(input_path)
    results, summary = verify_papers(payload, trust_arxiv_source=args.trust_arxiv_source, timeout=max(args.timeout, 5))
    output = {
        "date": args.date,
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "input": str(input_path),
        "summary": summary,
        "papers": results,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(output_path, output)
    if args.write_back:
        by_id = {r["canonical_id"]: r["verification"] for r in results}
        for paper in payload.get("candidates", []) or payload.get("papers", []) or []:
            cid = paper.get("canonical_id", "")
            if cid in by_id:
                paper["verification"] = by_id[cid]
        write_json(input_path, payload)
    print(f"[paperpilot] wrote {output_path}: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
