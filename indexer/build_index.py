"""Full index build. Writes to a temp directory and only promotes artifacts into
data/ when every step succeeds, so a partial index is never committed."""
from __future__ import annotations

import argparse
import csv
import io
import json
import shutil
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import bm25s
import faiss
import httpx
import numpy as np

from config import settings
from config import url_rules as ur
from indexer import chunker, crawler, embedder, sitemap
from indexer.extractor import extract

ARTIFACTS = ["faiss.index", "pages.json", "paragraphs.json", "manifest.json",
             "anchor_guide.json", "bm25"]

# Fail-closed thresholds. A stale index is recoverable; a silently truncated one
# looks healthy and produces wrong suggestions for weeks.
CRAWL_FAILURE_LIMIT = 0.10
EXTRACTION_FAILURE_LIMIT = 0.25
MIN_RETAINED_RATIO = 0.80


def fetch_anchor_guide() -> dict:
    """Supplementary data source. A failure here must never fail the build."""
    guide: dict[str, dict] = {}
    try:
        r = httpx.get(settings.ANCHOR_GUIDE_CSV, timeout=30, follow_redirects=True)
        r.raise_for_status()
        reader = csv.reader(io.StringIO(r.text))
        rows = list(reader)
        if not rows:
            return guide
        header = [h.strip().lower() for h in rows[0]]
        url_i = next((i for i, h in enumerate(header) if "url" in h or "target" in h), None)
        anc_i = next((i for i, h in enumerate(header) if "anchor" in h), None)
        if url_i is None or anc_i is None:
            print("  ! anchor guide columns not recognised; skipping")
            return guide
        for row in rows[1:]:
            if len(row) <= max(url_i, anc_i):
                continue
            u = ur.normalise(row[url_i].strip())
            a = row[anc_i].strip()
            if u and a:
                guide.setdefault(u, {"approved_anchors": [], "source": "anchor_guide"})
                if a not in guide[u]["approved_anchors"]:
                    guide[u]["approved_anchors"].append(a)
        print(f"  anchor guide: {len(guide)} target URLs")
    except Exception as exc:                                  # noqa: BLE001
        print(f"  ! anchor guide unavailable ({exc}); continuing without it")
    return guide


def build(limit: int | None = None, out_dir: Path | None = None,
          per_section: int | None = None) -> dict:
    started = datetime.now(timezone.utc)
    tmp = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="bil-index-"))
    tmp.mkdir(parents=True, exist_ok=True)

    print("1/6 discovering URLs from sitemap")
    records = sitemap.discover()
    if per_section:
        # Stratified sample so a smoke-test index still contains real articles
        # from every section, not just the alphabetically-first static pages.
        buckets: dict[str, list] = {}
        for r in records:
            buckets.setdefault(ur.section_of(r["sitemap_url"]), []).append(r)
        records = [r for rows in buckets.values() for r in rows[:per_section]]
        print(f"  stratified sample: {len(records)} URLs "
              f"({ {k: min(len(v), per_section) for k, v in buckets.items()} })")
    elif limit:
        records = records[:limit]
    lastmod = {ur.normalise(r["sitemap_url"]): r.get("lastmod") for r in records}
    urls = [ur.normalise(r["sitemap_url"]) for r in records]

    print(f"2/6 crawling {len(urls)} pages")
    fetched, stats = crawler.crawl(urls)
    if not fetched:
        raise RuntimeError("no pages fetched; aborting")
    if stats.failed > CRAWL_FAILURE_LIMIT * len(urls):
        raise RuntimeError(
            f"{stats.failed}/{len(urls)} pages failed (>10%); aborting without committing"
        )

    print("3/6 extracting content")
    pages, all_chunks, redirects = {}, [], {}
    extraction_failed, collisions = [], []
    for item in fetched:
        rec = extract(item["html"], item["final_url"])
        if rec is None:
            extraction_failed.append(item["final_url"])
            continue
        target = rec.get("canonical_url") or rec["url"]
        if not ur.is_allowed(target):
            target = rec["url"]
        rec["url"] = target
        rec["sitemap_url"] = item["sitemap_url"]
        rec["redirect_chain"] = item["redirect_chain"]
        rec["lastmod"] = lastmod.get(item["sitemap_url"])
        for hop in item["redirect_chain"]:
            n = ur.normalise(hop)
            if n and n != target:
                redirects[n] = target
        if target in pages:
            collisions.append((item["final_url"], target))
        blocks = rec.pop("_blocks")
        page_chunks = chunker.chunk_page(target, blocks)
        if not page_chunks:
            syn = chunker.synthetic_chunk(rec)
            if syn:
                page_chunks = [syn]
        all_chunks.extend(page_chunks)
        pages[target] = rec
    target_only = sum(1 for r in pages.values() if not r.get("body_available"))
    print(f"  pages={len(pages)} chunks={len(all_chunks)} redirects={len(redirects)} "
          f"target_only={target_only} extraction_failed={len(extraction_failed)} "
          f"canonical_collisions={len(collisions)}")
    if not all_chunks:
        raise RuntimeError("no eligible chunks produced; check the selectors")

    # A template change can let every page fetch with HTTP 200 and still yield
    # nothing usable. Crawl success alone does not prove the index is sound.
    if len(extraction_failed) > EXTRACTION_FAILURE_LIMIT * len(fetched):
        raise RuntimeError(
            f"{len(extraction_failed)}/{len(fetched)} pages fetched but produced no "
            f"usable record (>{EXTRACTION_FAILURE_LIMIT:.0%}). The site template has "
            "probably changed; check config/selectors.py. Aborting without committing.\n"
            f"  examples: {extraction_failed[:5]}"
        )
    if collisions:
        print(f"  ! {len(collisions)} pages collapsed onto an existing canonical URL")
        for src, tgt in collisions[:5]:
            print(f"      {src} -> {tgt}")

    # Guard against a silent shrink against the previous index.
    prev_path = settings.DATA / "manifest.json"
    if out_dir is None and prev_path.exists():
        try:
            prev = json.loads(prev_path.read_text("utf-8"))
            prev_pages = int(prev.get("pages", 0))
            if prev_pages and len(pages) < MIN_RETAINED_RATIO * prev_pages and not limit \
                    and not per_section:
                raise RuntimeError(
                    f"Page count dropped from {prev_pages} to {len(pages)} "
                    f"(<{MIN_RETAINED_RATIO:.0%} retained). Aborting without committing; "
                    "run with --per-section to investigate."
                )
        except json.JSONDecodeError:
            pass

    print("4/6 counting inbound links")
    inbound = defaultdict(int)
    for rec in pages.values():
        for tgt in set(rec["body_internal_links"]):
            tgt = redirects.get(tgt, tgt)
            if tgt in pages and tgt != rec["url"]:
                inbound[tgt] += 1
    for u, rec in pages.items():
        rec["inbound_link_count"] = inbound.get(u, 0)
    orphans = sum(1 for r in pages.values() if r["inbound_link_count"] == 0)
    print(f"  pages with zero inbound body links: {orphans}/{len(pages)}")

    print("5/6 embedding")
    texts = [chunker.embed_text(c) for c in all_chunks]
    mat = embedder.embed_passages(texts)
    embedder.assert_normalised(mat)
    index = faiss.IndexFlatIP(settings.EMBED_DIM)
    index.add(mat)
    faiss.write_index(index, str(tmp / "faiss.index"))

    print("6/6 building lexical index and writing artifacts")
    corpus_tokens = bm25s.tokenize(texts, stopwords="en", show_progress=False)
    retriever = bm25s.BM25()
    retriever.index(corpus_tokens, show_progress=False)
    retriever.save(str(tmp / "bm25"))

    guide = fetch_anchor_guide()

    manifest = {
        "embedding_runtime": "fastembed",
        "model_name": settings.MODEL_NAME,
        "embedding_dimension": settings.EMBED_DIM,
        "normalization": True,
        "query_prefix_applied_by": settings.QUERY_PREFIX_APPLIED_BY,
        "faiss_version": faiss.__version__,
        "built_at": started.isoformat(),
        "pages": len(pages),
        "chunks": len(all_chunks),
        "orphan_pages": orphans,
        "target_only_pages": target_only,
        "extraction_failed": len(extraction_failed),
        "canonical_collisions": [{"from": a, "to": b} for a, b in collisions],
        "build_seconds": None,
        "redirects": redirects,
        "lastmod": {u: lastmod.get(pages[u].get("sitemap_url")) for u in pages},
        "content_hash": {u: r["content_hash"] for u, r in pages.items()},
    }

    (tmp / "pages.json").write_text(json.dumps(pages, ensure_ascii=False), "utf-8")
    (tmp / "paragraphs.json").write_text(json.dumps(all_chunks, ensure_ascii=False), "utf-8")
    (tmp / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), "utf-8")
    (tmp / "anchor_guide.json").write_text(json.dumps(guide, ensure_ascii=False), "utf-8")

    if out_dir is None:
        settings.DATA.mkdir(parents=True, exist_ok=True)
        for name in ARTIFACTS:
            src, dst = tmp / name, settings.DATA / name
            if not src.exists():
                continue
            if dst.exists():
                shutil.rmtree(dst) if dst.is_dir() else dst.unlink()
            shutil.move(str(src), str(dst))
        print(f"promoted artifacts into {settings.DATA}")
    took = (datetime.now(timezone.utc) - started).total_seconds()
    manifest["build_seconds"] = round(took)
    (settings.DATA if out_dir is None else tmp).joinpath("manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), "utf-8")
    print(f"done in {took:.0f}s")
    return manifest


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                    help="index only the first N sitemap URLs (for smoke tests)")
    ap.add_argument("--per-section", type=int, default=None,
                    help="stratified smoke test: N URLs from each section")
    args = ap.parse_args()
    try:
        build(limit=args.limit, per_section=args.per_section)
    except Exception as exc:                                   # noqa: BLE001
        print(f"BUILD FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
