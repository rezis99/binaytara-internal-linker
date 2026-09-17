"""Orchestrator: an article in, two tables out."""
from __future__ import annotations

from datetime import datetime

import numpy as np

from config import settings
from config import url_rules as ur
from engine import anchor as anchor_mod
from engine import retrieval, rules
from indexer import chunker, embedder

SECTIONS = ["TCN", "IJCCD", "Blog", "Conference", "Project", "Static", "Contributor"]


def _sections_filter(page: dict, allowed: set[str] | None) -> bool:
    return allowed is None or page.get("section") in allowed


def _recommend_note(row: dict, page: dict, is_best: bool) -> str:
    bits = ["Recommended" if is_best else "Alternative"]
    bits.append(f"{page.get('inbound_link_count', 0)} inbound links")
    pub = (page.get("published_date") or "")[:4]
    if pub:
        bits.append(f"published {pub}")
    if row.get("extra_note"):
        bits.append(row["extra_note"])
    crowd = rules.crowding_note(page)
    if crowd:
        bits.append(crowd)
    return ". ".join(b for b in bits if b)


def links_to_give(article: dict, store: retrieval.Store,
                  allowed_sections: set[str] | None = None,
                  show_lower: bool = True,
                  deorphan: bool = False) -> list[dict]:
    """Where in this article to place links out to existing pages."""
    rows: list[dict] = []
    already = set(article.get("body_internal_links") or [])
    exclude = {article["url"]}

    placeable = [c for c in article["chunks"] if c.get("placement_ok", True)]
    for chunk in placeable:
        cands = retrieval.search_chunks(
            store, chunker.embed_text(chunk), exclude_urls=exclude)
        cands = retrieval.collapse_to_pages(cands, settings.CANDIDATES_PER_CHUNK)

        group: list[dict] = []
        for cand in cands:
            target = store.page(cand["url"])
            if target is None or not _sections_filter(target, allowed_sections):
                continue
            if not rules.url_ok(target["url"]):                    # R9
                continue
            if rules.already_linked_in_body(article, target["url"]):   # R7
                continue

            named_ok, name_note = rules.contributor_named(target, chunk["text"])  # R10
            if not named_ok:
                continue

            # Hard relevance gate BEFORE anchor work. A candidate below the
            # measured noise floor is not a weak suggestion, it is noise, and no
            # amount of anchor quality should rescue it.
            if cand["raw_cosine"] < settings.COSINE_HARD_MIN:
                continue

            best = anchor_mod.select(target, chunk["text"], store.guide)
            if not best or not rules.anchor_word_count_ok(best["anchor"]):   # R8
                continue

            # Asking a writer to rewrite a sentence is only worth it when the
            # topical match is strong. This is what stops "incorporate the
            # phrase 'Summer Volunteer Position' into this paragraph".
            if best["match_type"] == "Needs insertion" \
                    and cand["raw_cosine"] < settings.COSINE_INSERTION_MIN:
                continue

            score = rules.final_score(
                cand["semantic_score"], cand["lexical_score"], best["anchor_score"],
                deorphan=deorphan, inbound=target.get("inbound_link_count", 0),
                synthetic=not cand["chunk"].get("placement_ok", True),
                evidence=rules.keyword_evidence(
                    best["tier"], best["match_type"], best["anchor"]))

            keep, conf_note, force_lower = rules.conference_ok(target, score)  # R11
            if not keep:
                continue

            b = rules.band(score)
            if b is None:
                continue
            if force_lower:
                b = "Lower"

            level, why = rules.overlap(article, target, best["anchor"])
            group.append({
                "block_index": chunk["block_index"],
                "existing_sentence": chunk["text"],
                "modified_sentence": rules.modified_sentence(
                    chunk["text"], best["span"], best["anchor"],
                    target["url"], best["match_type"]),
                "anchor": best["anchor"],
                "target_url": target["url"],
                "target_title": target.get("h1") or target.get("title_clean") or "",
                "section": target.get("section", ""),
                "relevance": b,
                "match_type": best["match_type"],
                "overlap_level": level,
                "overlap_why": why,
                "score": score,
                "extra_note": ". ".join(x for x in (name_note, conf_note) if x),
                "_page": target,
            })

        group.sort(key=lambda r: -r["score"])
        for i, r in enumerate(group):
            r["notes"] = _recommend_note(r, r.pop("_page"), is_best=(i == 0))
        rows.extend(group)

    rows = rules.enforce_caps(rows)                                # R5, R6
    if not show_lower:
        rows = [r for r in rows if r["relevance"] in ("High", "Medium")]
    return rows


def links_to_receive(article: dict, store: retrieval.Store,
                     allowed_sections: set[str] | None = None,
                     show_lower: bool = True) -> list[dict]:
    """Which existing pages should add a link pointing to this article.

    The article is represented by its H1, its title, and its three most on-topic
    chunks, fused with RRF. A plain mean of all chunk embeddings would pull a
    multi-topic article toward a centroid representing none of its subjects.
    """
    queries: list[str] = []
    if article.get("h1"):
        queries.append(article["h1"])
    if article.get("title_clean") and article["title_clean"] != article.get("h1"):
        queries.append(article["title_clean"])

    placeable = [c for c in article["chunks"] if c.get("placement_ok", True)]
    if placeable and article.get("h1"):
        hvec = embedder.embed_queries([article["h1"]])
        cvec = embedder.embed_passages([chunker.embed_text(c) for c in placeable])
        sims = (cvec @ hvec[0]).tolist()
        top = sorted(zip(sims, placeable), key=lambda t: -t[0])[:3]
        queries.extend(c["text"] for _s, c in top)
    elif placeable:
        queries.extend(c["text"] for c in placeable[:3])

    fused: dict[str, dict] = {}
    for q in queries:
        cands = retrieval.search_chunks(store, q, exclude_urls={article["url"]})
        for rank, cand in enumerate(cands):
            key = cand["chunk"]["chunk_id"]
            entry = fused.setdefault(key, {**cand, "rrf": 0.0})
            entry["rrf"] += 1.0 / (settings.RRF_K + rank + 1)
            entry["semantic_score"] = max(entry["semantic_score"], cand["semantic_score"])
            entry["lexical_score"] = max(entry["lexical_score"], cand["lexical_score"])

    cands = retrieval.collapse_to_pages(
        sorted(fused.values(), key=lambda c: -c["rrf"]), settings.RECEIVE_SOURCE_PAGES)

    rows = []
    for cand in cands:
        source = store.page(cand["url"])
        chunk = cand["chunk"]
        if source is None or not _sections_filter(source, allowed_sections):
            continue
        if cand["raw_cosine"] < settings.COSINE_HARD_MIN:
            continue
        if not chunk.get("placement_ok", True):
            continue          # a hub page has no paragraph a link can go into
        if article.get("is_draft"):
            note_pub = ("Article not yet published; verify no existing link once "
                        "the URL is live")
        else:
            if rules.already_linked_in_body(source, article["url"]):     # R7
                continue
            note_pub = ""

        best = anchor_mod.select(article, chunk["text"], store.guide)
        if not best or not rules.anchor_word_count_ok(best["anchor"]):
            continue
        if best["match_type"] == "Needs insertion" \
                and cand["raw_cosine"] < settings.COSINE_INSERTION_MIN:
            continue

        score = rules.final_score(
            cand["semantic_score"], cand["lexical_score"], best["anchor_score"],
            evidence=rules.keyword_evidence(
                best["tier"], best["match_type"], best["anchor"]))
        b = rules.band(score)
        if b is None:
            continue
        level, why = rules.overlap(source, article, best["anchor"])
        notes = ". ".join(x for x in (note_pub, rules.crowding_note(source)) if x)
        rows.append({
            "source_url": source["url"],
            "source_title": source.get("h1") or source.get("title_clean") or "",
            "section": source.get("section", ""),
            "existing_sentence": chunk["text"],
            "modified_sentence": rules.modified_sentence(
                chunk["text"], best["span"], best["anchor"],
                article["url"], best["match_type"]),
            "anchor": best["anchor"],
            "relevance": b,
            "match_type": best["match_type"],
            "overlap_level": level,
            "overlap_why": why,
            "score": score,
            "notes": notes,
        })

    rows.sort(key=lambda r: -r["score"])
    if not show_lower:
        rows = [r for r in rows if r["relevance"] in ("High", "Medium")]
    return rows


def analyse(article: dict, store: retrieval.Store,
            allowed_sections: set[str] | None = None,
            show_lower: bool = True, deorphan: bool = False) -> dict:
    give = links_to_give(article, store, allowed_sections, show_lower, deorphan)
    receive = links_to_receive(article, store, allowed_sections, show_lower)
    return {
        "article": {
            "title": article.get("h1") or article.get("title_clean") or article["url"],
            "url": article["url"],
            "is_draft": article.get("is_draft", False),
            "word_count": article.get("word_count", 0),
            "benchmark": rules.benchmark(article.get("word_count", 0)),
            "existing_links": article.get("body_internal_links") or [],
            "eligible_paragraphs": len([c for c in article["chunks"]
                                        if c.get("placement_ok", True)]),
        },
        "give": give,
        "receive": receive,
        "index": {
            "built_at": store.manifest.get("built_at", ""),
            "pages": store.manifest.get("pages", 0),
            "age_days": round(retrieval.index_age_days(store), 1),
        },
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
