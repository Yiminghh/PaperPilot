#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ARXIV_API = "https://export.arxiv.org/api/query"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
ARXIV_NS = {"arxiv": "http://arxiv.org/schemas/atom"}
TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9\-]+")


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:
        raise SystemExit("Missing dependency: pyyaml. Run `python -m pip install pyyaml`.") from exc
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def clean_text(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in TOKEN_RE.findall(text) if len(t) > 1]


def normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def canonical_arxiv_id(source_id: str) -> str:
    tail = source_id.rstrip("/").split("/")[-1]
    tail = re.sub(r"v\d+$", "", tail)
    return f"arxiv:{tail}"


def flatten_interest(interest: dict[str, Any]) -> tuple[list[dict[str, str]], list[str], list[str]]:
    include = interest.get("include") or {}
    queries: list[dict[str, str]] = []
    for group, weight in [("primary", "primary"), ("secondary", "secondary"), ("query_expansion", "query")]:
        for text in include.get(group, []) or []:
            queries.append({"text": str(text), "group": weight})
    negative = interest.get("negative") or {}
    hard = [str(x).lower() for x in negative.get("hard_exclude", []) or []]
    soft = [str(x).lower() for x in negative.get("soft_downweight", []) or []]
    return queries, hard, soft


def fetch_arxiv_query(
    query: str,
    max_results: int,
    retries: int = 3,
    timeout: int = 30,
) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode(
        {
            "search_query": query,
            "start": 0,
            "max_results": max_results,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
    )
    url = f"{ARXIV_API}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "paperpilot/0.1 (mailto:y.huang24@imperial.ac.uk)"})
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                xml_data = resp.read()
            break
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code == 429 and attempt < retries:
                retry_after = exc.headers.get("Retry-After")
                wait = int(retry_after) if retry_after and retry_after.isdigit() else 10 * attempt
                print(f"[paperpilot] arXiv rate limited (429), retrying in {wait}s ({attempt}/{retries})", flush=True)
                time.sleep(wait)
                continue
            raise SystemExit(
                "arXiv API request failed with HTTP 429 rate limit. "
                "Wait and rerun, or reduce max_results/categories."
            ) from exc
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                wait = 5 * attempt
                print(
                    f"[paperpilot] arXiv request failed ({type(exc).__name__}), "
                    f"retrying in {wait}s ({attempt}/{retries})",
                    flush=True,
                )
                time.sleep(wait)
                continue
            raise SystemExit(f"arXiv API request failed: {type(exc).__name__}: {exc}") from exc
    else:
        raise SystemExit(f"arXiv API request failed: {last_error}")
    root = ET.fromstring(xml_data)
    papers: list[dict[str, Any]] = []
    for entry in root.findall("atom:entry", ATOM_NS):
        source_id = clean_text(entry.findtext("atom:id", namespaces=ATOM_NS))
        title = clean_text(entry.findtext("atom:title", namespaces=ATOM_NS))
        abstract = clean_text(entry.findtext("atom:summary", namespaces=ATOM_NS))
        published = clean_text(entry.findtext("atom:published", namespaces=ATOM_NS))
        updated = clean_text(entry.findtext("atom:updated", namespaces=ATOM_NS))
        authors = [
            clean_text(a.findtext("atom:name", namespaces=ATOM_NS))
            for a in entry.findall("atom:author", ATOM_NS)
        ]
        cats = [c.attrib.get("term", "") for c in entry.findall("atom:category", ATOM_NS)]
        pdf_url = ""
        for link in entry.findall("atom:link", ATOM_NS):
            if link.attrib.get("title") == "pdf" or link.attrib.get("type") == "application/pdf":
                pdf_url = link.attrib.get("href", "")
                break
        doi = ""
        doi_el = entry.find("arxiv:doi", ARXIV_NS)
        if doi_el is not None:
            doi = clean_text(doi_el.text)
        canonical_id = canonical_arxiv_id(source_id)
        papers.append(
            {
                "canonical_id": canonical_id,
                "source_id": source_id.rstrip("/").split("/")[-1],
                "source": "arxiv",
                "source_ids": [source_id.rstrip("/").split("/")[-1]],
                "title": title,
                "abstract": abstract,
                "authors": [a for a in authors if a],
                "published": published,
                "updated": updated,
                "url": f"https://arxiv.org/abs/{canonical_id.split(':', 1)[1]}",
                "pdf_url": pdf_url or f"https://arxiv.org/pdf/{canonical_id.split(':', 1)[1]}",
                "categories": [c for c in cats if c],
                "dedupe_keys": {
                    "arxiv_id": canonical_id.split(":", 1)[1],
                    "doi": doi,
                    "normalized_title": normalize_title(title),
                },
            }
        )
    return papers


def fetch_arxiv(
    categories: list[str],
    max_results: int,
    retries: int = 3,
    timeout: int = 30,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Fetch arXiv papers category-by-category.

    A single large OR query across many categories is more likely to hit arXiv's
    rate limiter. Category-level requests are slower but more predictable.
    """
    if not categories:
        return [], []
    per_category = max(1, math.ceil(max_results / len(categories)))
    merged: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    delay = float(os.getenv("PAPERFLOW_ARXIV_CATEGORY_DELAY", "5"))
    for index, category in enumerate(categories):
        if index > 0:
            time.sleep(delay)
        print(f"[paperpilot] arXiv category={category} max_results={per_category}", flush=True)
        try:
            papers = fetch_arxiv_query(
                f"cat:{category}",
                per_category,
                retries=retries,
                timeout=timeout,
            )
        except SystemExit as exc:
            message = f"{category}: {exc}"
            errors.append(message)
            print(f"[paperpilot][warn] skipped arXiv category after failure: {message}", flush=True)
            continue
        for paper in papers:
            cid = paper["canonical_id"]
            if cid in merged:
                existing_sources = set(merged[cid].get("source_ids", []))
                existing_sources.update(paper.get("source_ids", []))
                merged[cid]["source_ids"] = sorted(existing_sources)
                merged[cid]["categories"] = sorted(
                    set(merged[cid].get("categories", [])) | set(paper.get("categories", []))
                )
            else:
                merged[cid] = paper
    papers = list(merged.values())
    if not papers and errors:
        raise SystemExit("All arXiv category requests failed: " + "; ".join(errors))
    if errors:
        print(f"[paperpilot][warn] partial arXiv fetch; skipped {len(errors)} categories", flush=True)
    papers.sort(key=lambda p: p.get("published", ""), reverse=True)
    return papers[:max_results], errors


def parse_arxiv_date(value: str) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def filter_recent(papers: list[dict[str, Any]], days_window: int, today: dt.date) -> list[dict[str, Any]]:
    cutoff = dt.datetime.combine(today - dt.timedelta(days=days_window), dt.time.min, dt.UTC)
    out = []
    for paper in papers:
        published = parse_arxiv_date(paper.get("published", ""))
        if published is None or published >= cutoff:
            out.append(paper)
    return out


def text_for_retrieval(paper: dict[str, Any]) -> str:
    cats = " ".join(paper.get("categories") or [])
    return f"{paper.get('title', '')} {paper.get('abstract', '')} {cats}"


def is_excluded(paper: dict[str, Any], hard_terms: list[str]) -> tuple[bool, str]:
    text = text_for_retrieval(paper).lower()
    molecular_context = any(
        key in text for key in ["molecule", "molecular", "protein", "rna", "drug", "material"]
    )
    for term in hard_terms:
        if term and term in text:
            if term == "quantum chemistry" and molecular_context:
                return False, ""
            return True, term
    return False, ""


def has_any(text: str, terms: list[str]) -> bool:
    normalized_text = f" {normalize_title(text)} "
    for term in terms:
        normalized_term = normalize_title(term)
        if normalized_term and f" {normalized_term} " in normalized_text:
            return True
    return False


def category_policy_reason(paper: dict[str, Any], policies: dict[str, str]) -> str:
    if not policies:
        return ""
    categories = [str(c) for c in paper.get("categories") or []]
    text = text_for_retrieval(paper).lower()
    for selector, policy in policies.items():
        if not any(category == selector or category.startswith(f"{selector}.") for category in categories):
            continue
        if policy == "only_keep_if_llm_or_foundation_model_relevant_to_science_or_graphs":
            llm_terms = [
                "large language model",
                " llm ",
                "foundation model",
                "agent",
                "retrieval-augmented",
                "rag",
                "transformer",
            ]
            context_terms = [
                "science",
                "scientific",
                "graph",
                "molecular",
                "molecule",
                "protein",
                "rna",
                "biomolecule",
                "chemistry",
                "biology",
                "biomedical",
                "clinical",
            ]
            if has_any(f" {text} ", llm_terms) and has_any(text, context_terms):
                continue
            return policy
        if policy == "only_keep_if_geometric_3d_scientific_or_molecular_relevant":
            keep_terms = [
                "3d",
                "point cloud",
                "mesh",
                "geometric deep learning",
                "molecular",
                "molecule",
                "protein",
                "rna",
                "biomolecule",
                "chemistry",
                "scientific",
                "medical",
                "clinical",
                "microscopy",
                "cryo",
                "cell",
                "biological",
                "material",
                "drug",
            ]
            if has_any(text, keep_terms):
                continue
            return policy
        if policy == "keep_if_ai4science_or_representation_learning_relevant":
            ai_terms = [
                "machine learning",
                "deep learning",
                "neural",
                "foundation model",
                "language model",
                "representation",
                "prediction",
                "predicting",
                "predict",
                "generative",
                "diffusion",
                "graph",
                "transformer",
                "embedding",
                "qsar",
                "clustering",
                "classification",
                "regression",
                "classifier",
            ]
            if has_any(text, ai_terms):
                continue
            return policy
    return ""


def bm25_scores(
    papers: list[dict[str, Any]],
    queries: list[dict[str, str]],
    top_k: int,
) -> dict[str, dict[str, Any]]:
    docs = [tokenize(text_for_retrieval(p)) for p in papers]
    n_docs = len(docs)
    if not n_docs:
        return {}
    avgdl = sum(len(d) for d in docs) / max(n_docs, 1)
    df: Counter[str] = Counter()
    for doc in docs:
        df.update(set(doc))
    k1 = 1.5
    b = 0.75
    best: dict[str, dict[str, Any]] = {}
    for query in queries:
        q_tokens = tokenize(query["text"])
        if not q_tokens:
            continue
        scores: list[tuple[int, float]] = []
        for idx, doc in enumerate(docs):
            tf = Counter(doc)
            score = 0.0
            for token in q_tokens:
                if token not in tf:
                    continue
                idf = math.log(1 + (n_docs - df[token] + 0.5) / (df[token] + 0.5))
                denom = tf[token] + k1 * (1 - b + b * len(doc) / max(avgdl, 1e-9))
                score += idf * (tf[token] * (k1 + 1)) / denom
            if score > 0:
                scores.append((idx, score))
        scores.sort(key=lambda x: x[1], reverse=True)
        for rank, (idx, score) in enumerate(scores[:top_k], start=1):
            cid = papers[idx]["canonical_id"]
            entry = best.setdefault(
                cid, {"score": 0.0, "rank": rank, "matched_queries": [], "query_scores": {}}
            )
            if score > entry["score"]:
                entry["score"] = score
                entry["rank"] = rank
            entry["matched_queries"].append(query["text"])
            entry["query_scores"][query["text"]] = score
    return best


def dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def embedding_scores(
    papers: list[dict[str, Any]],
    queries: list[dict[str, str]],
    provider: str,
    embed_conf: dict[str, Any],
    top_k: int,
    min_score: float,
) -> dict[str, dict[str, Any]]:
    if provider == "none" or not papers or not queries:
        return {}
    if provider != "local":
        raise SystemExit(f"Embedding provider {provider!r} is not implemented in the MVP.")
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise SystemExit(
            "Missing dependency: sentence-transformers. "
            "Install optional deps or run with `--embedding-provider none` for a light test."
        ) from exc
    cache_dir = embed_conf.get("cache_dir")
    if cache_dir:
        os.environ.setdefault("HF_HOME", str(cache_dir))
        os.environ.setdefault("TRANSFORMERS_CACHE", str(cache_dir))
    model_name = str(embed_conf.get("model") or "BAAI/bge-m3")
    device = str(embed_conf.get("device") or "cpu")
    batch_size = int(embed_conf.get("batch_size") or 8)
    query_prefix = str(embed_conf.get("query_prefix") or "")
    document_prefix = str(embed_conf.get("document_prefix") or "")
    normalize = bool(embed_conf.get("normalize_embeddings", True))
    model = SentenceTransformer(model_name, device=device)
    doc_texts = [document_prefix + text_for_retrieval(p) for p in papers]
    query_texts = [query_prefix + q["text"] for q in queries]
    doc_emb = model.encode(
        doc_texts,
        batch_size=batch_size,
        convert_to_numpy=True,
        normalize_embeddings=normalize,
        show_progress_bar=True,
    )
    query_emb = model.encode(
        query_texts,
        batch_size=batch_size,
        convert_to_numpy=True,
        normalize_embeddings=normalize,
        show_progress_bar=False,
    )
    best: dict[str, dict[str, Any]] = {}
    for q_idx, query in enumerate(queries):
        scores = [(idx, float(doc_emb[idx].dot(query_emb[q_idx]))) for idx in range(len(papers))]
        scores.sort(key=lambda x: x[1], reverse=True)
        for rank, (idx, score) in enumerate(scores[:top_k], start=1):
            if score < min_score:
                continue
            cid = papers[idx]["canonical_id"]
            entry = best.setdefault(
                cid, {"score": 0.0, "rank": rank, "matched_queries": [], "query_scores": {}}
            )
            if score > entry["score"]:
                entry["score"] = score
                entry["rank"] = rank
            entry["matched_queries"].append(query["text"])
            entry["query_scores"][query["text"]] = score
    return best


def apply_priority_boost(paper: dict[str, Any], score: float, boosts: dict[str, Any]) -> float:
    text = text_for_retrieval(paper).lower()
    multiplier = 1.0
    if any(k in text for k in ["topological", "simplicial", "cell complex", "higher-order"]):
        multiplier *= float(boosts.get("topological_deep_learning", 1.0))
    if any(k in text for k in ["higher-order graph", "hypergraph", "simplicial"]):
        multiplier *= float(boosts.get("higher_order_graphs", 1.0))
    if any(k in text for k in ["molecular", "molecule", "rna", "biomolecule", "protein"]):
        multiplier *= float(boosts.get("molecular_or_rna_ai4science", 1.0))
    if "foundation model" in text:
        multiplier *= float(boosts.get("foundation_models_for_science", 1.0))
    if "large language model" in text or " llm " in f" {text} ":
        if not any(k in text for k in ["science", "scientific", "graph", "molecular", "rna"]):
            multiplier *= float(boosts.get("generic_llm_without_science_or_graph", 1.0))
    if "quantum" in text and not any(k in text for k in ["molecular", "molecule", "chemistry"]):
        multiplier *= float(boosts.get("quantum_related", 1.0))
    if "quantum chemistry" in text:
        multiplier *= float(boosts.get("quantum_chemistry_with_molecular_ml", 1.0))
    return score * multiplier


def rrf_rank(
    papers: list[dict[str, Any]],
    bm25: dict[str, dict[str, Any]],
    emb: dict[str, dict[str, Any]],
    conf: dict[str, Any],
) -> list[dict[str, Any]]:
    rrf_k = int(conf.get("rrf_k") or 60)
    boosts = conf.get("priority_boost") or {}
    by_id = {p["canonical_id"]: p for p in papers}
    ids = set(bm25) | set(emb)
    candidates: list[dict[str, Any]] = []
    for cid in ids:
        paper = dict(by_id[cid])
        score = 0.0
        if cid in bm25:
            score += 1.0 / (rrf_k + int(bm25[cid]["rank"]))
        if cid in emb:
            score += 1.0 / (rrf_k + int(emb[cid]["rank"]))
        score = apply_priority_boost(paper, score, boosts)
        matched = []
        query_scores: dict[str, float] = {}
        for source_scores in (bm25.get(cid, {}), emb.get(cid, {})):
            for query, query_score in source_scores.get("query_scores", {}).items():
                query_scores[query] = max(query_scores.get(query, 0.0), float(query_score))
        matched_limit = int(conf.get("matched_queries_per_paper") or 8)
        matched.extend(
            query for query, _score in sorted(query_scores.items(), key=lambda item: item[1], reverse=True)
        )
        paper["matched_queries"] = matched[:matched_limit]
        paper["query_scores"] = {query: query_scores[query] for query in paper["matched_queries"]}
        paper["bm25_score"] = float(bm25.get(cid, {}).get("score", 0.0))
        paper["embedding_score"] = float(emb.get(cid, {}).get("score", 0.0))
        paper["score"] = score
        paper["final_score"] = score
        paper["rrf_score"] = score
        candidates.append(paper)
    candidates.sort(key=lambda p: p["rrf_score"], reverse=True)
    return candidates


def read_seen(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/paper-daily-config.yaml")
    parser.add_argument("--interest", default="config/interest-profile.yaml")
    parser.add_argument("--negative", default="config/negative-keywords.yaml")
    parser.add_argument("--output", required=True)
    parser.add_argument("--raw-output", default="")
    parser.add_argument("--obsidian-output", default="")
    parser.add_argument("--embedding-provider", default=os.getenv("PAPERFLOW_EMBED_PROVIDER", ""))
    parser.add_argument("--max-results", type=int, default=0)
    parser.add_argument("--days-window", type=int, default=0)
    parser.add_argument("--arxiv-retries", type=int, default=3)
    parser.add_argument("--arxiv-timeout", type=int, default=30)
    parser.add_argument("--date", default="")
    parser.add_argument("--include-seen", action="store_true")
    args = parser.parse_args()

    project_root = Path.cwd()
    config = load_yaml(project_root / args.config)
    interest = load_yaml(project_root / args.interest)
    negative = load_yaml(project_root / args.negative)
    today = dt.date.fromisoformat(args.date) if args.date else dt.date.today()

    arxiv_conf = config.get("arxiv") or {}
    retrieval_conf = config.get("retrieval") or {}
    embed_conf = config.get("embedding") or {}
    state_conf = config.get("state") or {}
    categories = [str(x) for x in arxiv_conf.get("categories", [])]
    max_results = args.max_results or int(arxiv_conf.get("max_results") or 300)
    days_window = args.days_window or int(arxiv_conf.get("days_window") or 3)
    retries = args.arxiv_retries if args.arxiv_retries != 3 else int(arxiv_conf.get("retries") or 3)
    timeout = args.arxiv_timeout if args.arxiv_timeout != 30 else int(arxiv_conf.get("timeout_seconds") or 30)
    if "PAPERFLOW_ARXIV_CATEGORY_DELAY" not in os.environ and arxiv_conf.get("category_delay_seconds") is not None:
        os.environ["PAPERFLOW_ARXIV_CATEGORY_DELAY"] = str(arxiv_conf.get("category_delay_seconds"))
    provider = args.embedding_provider or str(embed_conf.get("provider") or "local")
    queries, hard_exclude, _soft = flatten_interest(interest)
    hard_exclude.extend(str(x).lower() for x in negative.get("hard_exclude", []) or [])

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path = Path(args.raw_output) if args.raw_output else output_path.with_name("raw.json")
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    seen_path = project_root / str(state_conf.get("seen_path") or "state/seen-papers.txt")
    seen = read_seen(seen_path) if not args.include_seen else set()

    print(f"[paperpilot] Fetching arXiv categories={categories} max_results={max_results}", flush=True)
    raw, fetch_errors = fetch_arxiv(
        categories,
        max_results=max_results,
        retries=max(retries, 1),
        timeout=max(timeout, 5),
    )
    raw_path.write_text(
        json.dumps({"date": str(today), "skipped_categories": fetch_errors, "papers": raw}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    papers = filter_recent(raw, days_window, today=today)
    filtered: list[dict[str, Any]] = []
    seen_count = 0
    hard_excluded_count = 0
    category_policy_excluded_count = 0
    for paper in papers:
        if paper["canonical_id"] in seen:
            seen_count += 1
            continue
        excluded, reason = is_excluded(paper, hard_exclude)
        if excluded:
            paper["excluded_reason"] = reason
            hard_excluded_count += 1
            continue
        policy_reason = category_policy_reason(paper, retrieval_conf.get("category_policy") or {})
        if policy_reason:
            paper["excluded_reason"] = policy_reason
            category_policy_excluded_count += 1
            continue
        filtered.append(paper)

    print(f"[paperpilot] recent={len(papers)} after_seen_and_exclude={len(filtered)}", flush=True)
    bm25 = bm25_scores(filtered, queries, top_k=int(retrieval_conf.get("bm25_top_k") or 50))
    emb = embedding_scores(
        filtered,
        queries,
        provider=provider,
        embed_conf=embed_conf,
        top_k=int(retrieval_conf.get("embedding_top_k") or 40),
        min_score=float(retrieval_conf.get("embedding_min_score") or 0.0),
    )
    ranked = rrf_rank(filtered, bm25, emb, retrieval_conf)
    final_k = int(retrieval_conf.get("final_top_k") or 100)
    candidates = ranked[:final_k]
    payload = {
        "date": str(today),
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "source": "arxiv",
        "embedding_provider": provider,
        "embedding_model": embed_conf.get("model"),
        "query_count": len(queries),
        "raw_count": len(raw),
        "recent_count": len(papers),
        "seen_count": seen_count,
        "hard_excluded_count": hard_excluded_count,
        "category_policy_excluded_count": category_policy_excluded_count,
        "candidate_count": len(candidates),
        "skipped_categories": fetch_errors,
        "candidates": candidates,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[paperpilot] wrote {output_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
