#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any

from .config_utils import DEFAULT_CONFIG, load_config, project_root, resolve_path
from .paperpilot_utils import canonical_arxiv_id, clean_text, normalize_title, project_path_arg, write_json


ARXIV_API = "https://export.arxiv.org/api/query"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
ARXIV_NS = {"arxiv": "http://arxiv.org/schemas/atom"}
TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9\-]+")


def load_yaml_file(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:
        raise SystemExit("Missing dependency: pyyaml. Run `python -m pip install pyyaml`.") from exc
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in TOKEN_RE.findall(text) if len(t) > 1]


def flatten_interest(interest: dict[str, Any]) -> list[dict[str, str]]:
    include = interest.get("include") or {}
    queries: list[dict[str, str]] = []
    for group, weight in [("primary", "primary"), ("secondary", "secondary"), ("query_expansion", "query")]:
        for text in include.get(group, []) or []:
            queries.append({"text": str(text), "group": weight})
    return queries


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
    delay = float(os.getenv("PAPERPILOT_ARXIV_CATEGORY_DELAY", "5"))
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


def term_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).lower() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.lower()]
    return []


def term_groups(value: Any) -> list[list[str]]:
    if isinstance(value, dict):
        value = value.get("all") or value.get("term_groups") or []
    if not isinstance(value, list):
        return []
    groups = []
    for item in value:
        terms = term_list(item)
        if terms:
            groups.append(terms)
    return groups


def term_exceptions(negative: dict[str, Any]) -> dict[str, list[str]]:
    exceptions = {}
    raw = negative.get("context_exceptions") or {}
    for term, rule in raw.items():
        if isinstance(rule, dict):
            terms = term_list(rule.get("allow_if_any"))
        else:
            terms = term_list(rule)
        if terms:
            exceptions[str(term).lower()] = terms
    return exceptions


def should_skip_negative_term(term: str, text: str, exceptions: dict[str, list[str]]) -> bool:
    return has_any(text, exceptions.get(term, []))


def is_excluded(
    paper: dict[str, Any],
    hard_terms: list[str],
    exceptions: dict[str, list[str]] | None = None,
) -> tuple[bool, str]:
    text = text_for_retrieval(paper).lower()
    exceptions = exceptions or {}
    for term in hard_terms:
        if term and term in text:
            if should_skip_negative_term(term, text, exceptions):
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


def category_policy_reason(
    paper: dict[str, Any],
    policies: dict[str, str],
    policy_rules: dict[str, Any] | None = None,
) -> str:
    if not policies:
        return ""
    categories = [str(c) for c in paper.get("categories") or []]
    text = text_for_retrieval(paper).lower()
    policy_rules = policy_rules or {}
    for selector, policy in policies.items():
        if not any(category == selector or category.startswith(f"{selector}.") for category in categories):
            continue
        groups = term_groups(policy_rules.get(policy))
        if not groups:
            continue
        if all(has_any(text, terms) for terms in groups):
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
    doc_tfs = [Counter(doc) for doc in docs]
    k1 = 1.5
    b = 0.75
    best: dict[str, dict[str, Any]] = {}
    for query in queries:
        q_tokens = tokenize(query["text"])
        if not q_tokens:
            continue
        scores: list[tuple[int, float]] = []
        for idx, doc in enumerate(docs):
            tf = doc_tfs[idx]
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


def normalized_vectors(vectors: list[list[float]]) -> list[list[float]]:
    out = []
    for vector in vectors:
        norm = math.sqrt(sum(x * x for x in vector))
        out.append([x / norm for x in vector] if norm else vector)
    return out


def embedding_api_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/embeddings"):
        return base
    return f"{base}/embeddings"


def api_embeddings(texts: list[str], embed_conf: dict[str, Any]) -> list[list[float]]:
    base_url = (
        os.getenv("PAPERPILOT_EMBED_BASE_URL")
        or os.getenv("EMBED_BASE_URL")
        or str(embed_conf.get("api_base_url") or "")
    )
    if not base_url:
        raise SystemExit("Embedding provider 'api' requires PAPERPILOT_EMBED_BASE_URL or embedding.api_base_url.")
    key_env = str(embed_conf.get("api_key_env") or "PAPERPILOT_EMBED_API_KEY")
    api_key = os.getenv(key_env) or os.getenv("PAPERPILOT_EMBED_API_KEY") or os.getenv("OPENAI_API_KEY")
    model_name = (
        os.getenv("PAPERPILOT_EMBED_MODEL")
        or os.getenv("EMBED_MODEL")
        or str(embed_conf.get("api_model") or embed_conf.get("model") or "")
    )
    if not model_name:
        raise SystemExit("Embedding provider 'api' requires PAPERPILOT_EMBED_MODEL or embedding.model.")
    batch_size = int(embed_conf.get("batch_size") or 64)
    url = embedding_api_url(base_url)
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    vectors: list[list[float]] = []
    for start in range(0, len(texts), max(batch_size, 1)):
        batch = texts[start : start + max(batch_size, 1)]
        payload: dict[str, Any] = {"model": model_name, "input": batch}
        if embed_conf.get("dimensions"):
            payload["dimensions"] = int(embed_conf["dimensions"])
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=int(embed_conf.get("api_timeout_seconds") or 60)) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise SystemExit(f"Embedding API request failed: {type(exc).__name__}: {exc}") from exc
        items = sorted(data.get("data") or [], key=lambda item: int(item.get("index", 0)))
        if len(items) != len(batch):
            raise SystemExit(f"Embedding API returned {len(items)} vectors for {len(batch)} texts.")
        vectors.extend([list(map(float, item["embedding"])) for item in items])
    return vectors


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
    query_prefix = str(embed_conf.get("query_prefix") or "")
    document_prefix = str(embed_conf.get("document_prefix") or "")
    normalize = bool(embed_conf.get("normalize_embeddings", True))
    doc_texts = [document_prefix + text_for_retrieval(p) for p in papers]
    query_texts = [query_prefix + q["text"] for q in queries]
    if provider == "local":
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
        model = SentenceTransformer(model_name, device=device)
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
    elif provider == "api":
        doc_emb = api_embeddings(doc_texts, embed_conf)
        query_emb = api_embeddings(query_texts, embed_conf)
        if normalize:
            doc_emb = normalized_vectors(doc_emb)
            query_emb = normalized_vectors(query_emb)
    else:
        raise SystemExit(f"Embedding provider {provider!r} is not supported. Use local, api, or none.")
    best: dict[str, dict[str, Any]] = {}
    for q_idx, query in enumerate(queries):
        scores = [
            (idx, float(sum(x * y for x, y in zip(doc_emb[idx], query_emb[q_idx]))))
            for idx in range(len(papers))
        ]
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


def boost_rule_matches(text: str, rule: Any) -> bool:
    if isinstance(rule, dict):
        any_terms = term_list(rule.get("any"))
        none_terms = term_list(rule.get("none"))
    else:
        any_terms = term_list(rule)
        none_terms = []
    if any_terms and not has_any(text, any_terms):
        return False
    if none_terms and has_any(text, none_terms):
        return False
    return bool(any_terms or none_terms)


def apply_priority_boost(
    paper: dict[str, Any],
    score: float,
    boosts: dict[str, Any],
    boost_rules: dict[str, Any] | None = None,
) -> float:
    text = text_for_retrieval(paper).lower()
    multiplier = 1.0
    for key, rule in (boost_rules or {}).items():
        if boost_rule_matches(text, rule):
            multiplier *= float(boosts.get(key, 1.0))
    return score * multiplier


def matched_soft_downweight_terms(
    paper: dict[str, Any],
    soft_terms: list[str],
    exceptions: dict[str, list[str]] | None = None,
) -> list[str]:
    text = text_for_retrieval(paper).lower()
    exceptions = exceptions or {}
    matched = []
    for term in soft_terms:
        if not term or term not in text:
            continue
        if should_skip_negative_term(term, text, exceptions):
            continue
        matched.append(term)
    return matched


def rrf_rank(
    papers: list[dict[str, Any]],
    bm25: dict[str, dict[str, Any]],
    emb: dict[str, dict[str, Any]],
    conf: dict[str, Any],
    soft_downweight_terms: list[str] | None = None,
    exceptions: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    rrf_k = int(conf.get("rrf_k") or 60)
    boosts = conf.get("priority_boost") or {}
    soft_terms = soft_downweight_terms or []
    soft_multiplier = float(conf.get("soft_downweight_multiplier") or 0.75)
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
        score = apply_priority_boost(paper, score, boosts, conf.get("priority_boost_rules") or {})
        soft_matches = matched_soft_downweight_terms(paper, soft_terms, exceptions=exceptions)
        if soft_matches:
            score *= soft_multiplier
            paper["soft_downweight_terms"] = soft_matches
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
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--interest", default="config/interest-profile.yaml")
    parser.add_argument("--negative", default="config/negative-keywords.yaml")
    parser.add_argument("--output", required=True)
    parser.add_argument("--raw-output", default="")
    parser.add_argument(
        "--embedding-provider",
        default=os.getenv("PAPERPILOT_EMBED_PROVIDER", ""),
    )
    parser.add_argument("--max-results", type=int, default=0)
    parser.add_argument("--days-window", type=int, default=0)
    parser.add_argument("--arxiv-retries", type=int, default=3)
    parser.add_argument("--arxiv-timeout", type=int, default=30)
    parser.add_argument("--date", default="")
    parser.add_argument("--include-seen", action="store_true")
    args = parser.parse_args()

    root = project_root()
    config = load_config(args.config)
    interest = load_yaml_file(project_path_arg(args.interest, root))
    negative = load_yaml_file(project_path_arg(args.negative, root))
    today = dt.date.fromisoformat(args.date) if args.date else dt.date.today()

    arxiv_conf = config.get("arxiv") or {}
    retrieval_conf = config.get("retrieval") or {}
    embed_conf = config.get("embedding") or {}
    if embed_conf.get("cache_dir"):
        embed_conf["cache_dir"] = str(resolve_path(config, "embedding.cache_dir", ".cache/huggingface"))
    categories = [str(x) for x in arxiv_conf.get("categories", [])]
    max_results = args.max_results or int(arxiv_conf.get("max_results") or 300)
    days_window = args.days_window or int(arxiv_conf.get("days_window") or 3)
    retries = args.arxiv_retries if args.arxiv_retries != 3 else int(arxiv_conf.get("retries") or 3)
    timeout = args.arxiv_timeout if args.arxiv_timeout != 30 else int(arxiv_conf.get("timeout_seconds") or 30)
    if "PAPERPILOT_ARXIV_CATEGORY_DELAY" not in os.environ and arxiv_conf.get("category_delay_seconds") is not None:
        os.environ["PAPERPILOT_ARXIV_CATEGORY_DELAY"] = str(arxiv_conf.get("category_delay_seconds"))
    provider = args.embedding_provider or str(embed_conf.get("provider") or "local")
    if provider == "api":
        embedding_model_label = (
            os.getenv("PAPERPILOT_EMBED_MODEL")
            or os.getenv("EMBED_MODEL")
            or str(embed_conf.get("api_model") or embed_conf.get("model") or "")
        )
    else:
        embedding_model_label = str(embed_conf.get("model") or "")
    queries = flatten_interest(interest)
    hard_exclude = [str(x).lower() for x in negative.get("hard_exclude", []) or []]
    soft_downweight = [str(x).lower() for x in negative.get("soft_downweight", []) or []]
    exceptions = term_exceptions(negative)

    output_path = Path(args.output).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path = Path(args.raw_output).expanduser() if args.raw_output else output_path.with_name("raw.json")
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    seen_path = resolve_path(config, "paths.seen_path", "state/seen-papers.txt")
    include_seen = args.include_seen or os.getenv("PAPERPILOT_INCLUDE_SEEN", "").lower() in {"1", "true", "yes"}
    seen = read_seen(seen_path) if not include_seen else set()

    print(f"[paperpilot] Fetching arXiv categories={categories} max_results={max_results}", flush=True)
    raw, fetch_errors = fetch_arxiv(
        categories,
        max_results=max_results,
        retries=max(retries, 1),
        timeout=max(timeout, 5),
    )
    write_json(raw_path, {"date": str(today), "skipped_categories": fetch_errors, "papers": raw})
    papers = filter_recent(raw, days_window, today=today)
    filtered: list[dict[str, Any]] = []
    seen_count = 0
    hard_excluded_count = 0
    category_policy_excluded_count = 0
    for paper in papers:
        if paper["canonical_id"] in seen:
            seen_count += 1
            continue
        excluded, reason = is_excluded(paper, hard_exclude, exceptions=exceptions)
        if excluded:
            paper["excluded_reason"] = reason
            hard_excluded_count += 1
            continue
        policy_reason = category_policy_reason(
            paper,
            retrieval_conf.get("category_policy") or {},
            retrieval_conf.get("category_policy_rules") or {},
        )
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
    ranked = rrf_rank(
        filtered,
        bm25,
        emb,
        retrieval_conf,
        soft_downweight_terms=soft_downweight,
        exceptions=exceptions,
    )
    final_k = int(retrieval_conf.get("final_top_k") or 100)
    candidates = ranked[:final_k]
    payload = {
        "date": str(today),
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "source": "arxiv",
        "embedding_provider": provider,
        "embedding_model": embedding_model_label,
        "query_count": len(queries),
        "raw_count": len(raw),
        "recent_count": len(papers),
        "seen_count": seen_count,
        "hard_excluded_count": hard_excluded_count,
        "category_policy_excluded_count": category_policy_excluded_count,
        "bm25_match_count": len(bm25),
        "embedding_match_count": len(emb),
        "soft_downweighted_count": sum(1 for item in candidates if item.get("soft_downweight_terms")),
        "candidate_count": len(candidates),
        "skipped_categories": fetch_errors,
        "candidates": candidates,
    }
    write_json(output_path, payload)
    if not candidates and seen_count:
        print(
            "[paperpilot][warn] no candidates after seen filtering; "
            "for same-day reruns set PAPERPILOT_INCLUDE_SEEN=1 or pass --include-seen",
            flush=True,
        )
    print(f"[paperpilot] wrote {output_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
