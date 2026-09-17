"""Hybrid retrieval: dense (FAISS) plus lexical (BM25S), fused with Reciprocal
Rank Fusion.

RRF is used for CANDIDATE GENERATION because it needs no score normalisation
between two incomparable scales. Ranking of the survivors is a separate step in
scoring.py and does use a weighted sum, over explicitly normalised inputs.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

import bm25s
import faiss
import numpy as np

from config import settings
from engine.anchor import expand, tokens
from indexer import embedder


@dataclass
class Store:
    pages: dict
    chunks: list
    manifest: dict
    guide: dict
    index: object
    bm25: object
    by_url: dict = field(default_factory=dict)

    def page(self, url: str) -> dict | None:
        return self.pages.get(url)


def load(data_dir=None) -> Store:
    d = data_dir or settings.DATA
    pages = json.loads((d / "pages.json").read_text("utf-8"))
    chunks = json.loads((d / "paragraphs.json").read_text("utf-8"))
    manifest = json.loads((d / "manifest.json").read_text("utf-8"))
    try:
        guide = json.loads((d / "anchor_guide.json").read_text("utf-8"))
    except FileNotFoundError:
        guide = {}

    # Refuse to serve an index built with a different embedding contract rather
    # than returning silently wrong similarity scores.
    if manifest.get("model_name") != settings.MODEL_NAME:
        raise RuntimeError(
            f"Index was built with {manifest.get('model_name')} but settings "
            f"specify {settings.MODEL_NAME}. Rebuild the index."
        )
    if int(manifest.get("embedding_dimension", 0)) != settings.EMBED_DIM:
        raise RuntimeError("Index dimension does not match settings.EMBED_DIM.")

    index = faiss.read_index(str(d / "faiss.index"))
    bm25 = bm25s.BM25.load(str(d / "bm25"), load_corpus=False)

    by_url: dict[str, list[int]] = {}
    for i, c in enumerate(chunks):
        by_url.setdefault(c["url"], []).append(i)
    return Store(pages, chunks, manifest, guide, index, bm25, by_url)


def index_age_days(store: Store) -> float:
    try:
        built = datetime.fromisoformat(store.manifest["built_at"])
    except Exception:                                          # noqa: BLE001
        return 999.0
    if built.tzinfo is None:
        built = built.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - built).total_seconds() / 86400.0


def _expanded_tokens(text: str) -> list[str]:
    """Query tokens plus synonym expansions, so BM25 can bridge abbreviations."""
    base = tokens(text)
    extra: list[str] = []
    for n in range(1, 5):
        for i in range(len(base) - n + 1):
            phrase = " ".join(base[i:i + n])
            for alt in expand(phrase):
                extra.extend(tokens(alt))
    return base + extra


def _rrf(rank_lists: list[list[int]]) -> dict[int, float]:
    scores: dict[int, float] = {}
    for lst in rank_lists:
        for rank, idx in enumerate(lst):
            scores[idx] = scores.get(idx, 0.0) + 1.0 / (settings.RRF_K + rank + 1)
    return scores


def search_chunks(store: Store, query_text: str, exclude_urls: set[str],
                  k: int | None = None) -> list[dict]:
    """Candidate chunks for one query string, as dicts with raw sub-scores."""
    k = k or settings.CANDIDATES_PER_CHUNK
    n_chunks = len(store.chunks)
    if n_chunks == 0:
        return []

    qvec = embedder.embed_queries([query_text])
    dense_k = min(settings.DENSE_K, n_chunks)
    sims, ids = store.index.search(qvec, dense_k)
    dense_ids = [int(i) for i in ids[0] if i >= 0]
    dense_sim = {int(i): float(s) for i, s in zip(ids[0], sims[0]) if i >= 0}

    lex_ids, lex_raw = [], {}
    try:
        qt = bm25s.tokenize([" ".join(_expanded_tokens(query_text))],
                            stopwords="en", show_progress=False)
        lk = min(settings.LEXICAL_K, n_chunks)
        res, sc = store.bm25.retrieve(qt, k=lk, show_progress=False)
        lex_ids = [int(i) for i in res[0]]
        lex_raw = {int(i): float(s) for i, s in zip(res[0], sc[0])}
    except Exception:                                          # noqa: BLE001
        pass

    fused = _rrf([dense_ids, lex_ids])

    # Per-query normalisation of BM25. Never a global constant, never carried
    # between queries: BM25 is unbounded and not comparable across queries.
    lex_max = max(lex_raw.values()) if lex_raw else 0.0

    out = []
    for idx, rrf_score in sorted(fused.items(), key=lambda kv: -kv[1]):
        c = store.chunks[idx]
        if c["url"] in exclude_urls:
            continue
        raw_cos = dense_sim.get(idx, 0.0)
        out.append({
            "chunk_index": idx,
            "chunk": c,
            "url": c["url"],
            "rrf": rrf_score,
            "raw_cosine": raw_cos,
            "semantic_score": (raw_cos + 1.0) / 2.0,      # map [-1,1] to [0,1]
            "raw_bm25": lex_raw.get(idx, 0.0),
            "lexical_score": (lex_raw.get(idx, 0.0) / lex_max) if lex_max else 0.0,
        })
    return out[: max(k * 3, k)]


def collapse_to_pages(cands: list[dict], limit: int) -> list[dict]:
    """Best chunk per page. Ties within 0.01 break on lowest chunk index, so an
    overlapping chunk pair never yields two suggestions for one sentence."""
    best: dict[str, dict] = {}
    for c in cands:
        cur = best.get(c["url"])
        if cur is None:
            best[c["url"]] = c
            continue
        if c["rrf"] > cur["rrf"] + 0.01:
            best[c["url"]] = c
        elif abs(c["rrf"] - cur["rrf"]) <= 0.01 and c["chunk_index"] < cur["chunk_index"]:
            best[c["url"]] = c
    return sorted(best.values(), key=lambda x: -x["rrf"])[:limit]
