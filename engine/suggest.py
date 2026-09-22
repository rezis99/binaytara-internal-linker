"""Orchestrator: an article in, two tables out.

v3 changes
----------
1. Keyword scan: extract disease terms from the article, grep every indexed
   page's body text, boost pages that mention the article's core terms.
2. Same-topic detection: pairwise H1 Jaccard similarity surfaces articles about
   the same disease that the embedding model misses.
3. LLM rewriting: "Needs insertion" rows get a proposed rewrite instead of a
   manual instruction, when an LLM provider is configured.
4. Recalibrated thresholds: lower hard gate, wider bands, keyword weight.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np

from config import settings
from config import url_rules as ur
from engine import anchor as anchor_mod
from engine import cannibalization_data, keyword_scan, retrieval, rules
from engine import llm_rewrite
from indexer import chunker, embedder

SECTIONS = ["TCN", "IJCCD", "Blog", "Conference", "Project", "Static", "Contributor"]


def _review_context(article: dict, target: dict, cand: dict,
                    kw: float, ts: float, match_type: str) -> str:
    """v5: build a short context string that tells Claude WHY the tool suggested
    this link, so Claude can judge whether the match is genuine or a false
    positive from shared oncology vocabulary.

    This goes into a 'Review Context' column in the Excel. When the user uploads
    the Excel to Claude, the skill reads this column to make faster, more
    accurate relevance decisions.
    """
    src_topic = article.get("h1") or article.get("title_clean") or ""
    tgt_topic = target.get("h1") or target.get("title_clean") or ""
    parts = [f"Source: {src_topic[:80]}", f"Target: {tgt_topic[:80]}"]

    basis = []
    if cand.get("raw_cosine", 0) >= 0.65:
        basis.append(f"embedding similarity {cand['raw_cosine']:.2f}")
    if kw >= 0.15:
        basis.append(f"keyword overlap {kw:.0%}")
    if ts >= 0.40:
        basis.append(f"title similarity {ts:.0%}")
    if match_type in ("Exact in text", "Synonym in text"):
        basis.append(f"anchor phrase found in article text")
    if not basis:
        basis.append("weak signals only")
    parts.append(f"Match basis: {'; '.join(basis)}")

    # v5b: flag if the anchor matches a planned hub page
    anchor = ""
    if "anchor" in str(cand):
        anchor = cand.get("anchor", "")
    hub_note = keyword_scan.hub_page_note(
        target.get("h1") or target.get("title_clean") or "")
    if hub_note and not hub_note.startswith("HUB_LIVE:"):
        parts.append(hub_note)

    return ". ".join(parts)


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
    if row.get("keyword_note"):
        bits.append(row["keyword_note"])
    if row.get("title_sim_note"):
        bits.append(row["title_sim_note"])
    # v4: journal articles should be placed in a references section, not body.
    if row.get("is_journal_target"):
        bits.append("IJCCD research paper: consider placing in references or "
                     "'Works discussed' section rather than body prose")
    return ". ".join(b for b in bits if b)


def links_to_give(article: dict, store: retrieval.Store,
                  allowed_sections: set[str] | None = None,
                  show_lower: bool = True,
                  deorphan: bool = False) -> list[dict]:
    """Where in this article to place links out to existing pages."""
    rows: list[dict] = []
    already = set(article.get("body_internal_links") or [])
    exclude = {article["url"]}

    # v3/v4: pre-compute keyword scan, title similarity, awareness matching.
    body_texts = getattr(store, "body_texts", {}) or {}
    article_terms = keyword_scan.extract_terms(article)
    kw_scores = keyword_scan.scan_pages(article_terms, body_texts, exclude | already)
    title_sims = keyword_scan.title_similarity(article, store.pages, exclude | already)
    # v4: awareness-month pages and conference recaps for this article's diseases.
    aware = keyword_scan.awareness_and_conference_matches(
        article, store.pages, exclude | already)
    cmap = getattr(store, "cannibalization", {}) or {}

    # Collect all candidate URLs from three sources:
    # 1. Embedding retrieval (per-chunk)
    # 2. Keyword scan (page-level)
    # 3. Title similarity (page-level)
    # For 2 and 3, we need to pick chunks from those pages to place the link in.

    placeable = [c for c in article["chunks"] if c.get("placement_ok", True)]

    # Track which pages we already evaluated from embedding retrieval.
    evaluated_targets: set[str] = set()

    for chunk in placeable:
        cands = retrieval.search_chunks(
            store, chunker.embed_text(chunk), exclude_urls=exclude)
        cands = retrieval.collapse_to_pages(cands, settings.CANDIDATES_PER_CHUNK)

        group: list[dict] = []
        for cand in cands:
            target = store.page(cand["url"])
            if target is None or not _sections_filter(target, allowed_sections):
                continue
            if not rules.url_ok(target["url"]):
                continue
            if rules.already_linked_in_body(article, target["url"]):
                continue

            evaluated_targets.add(target["url"])

            # v4: IJCCD articles as link targets need special handling. A journal
            # paper should only be suggested when its core disease matches the
            # source article's topic (not just any n-gram overlap), and the
            # placement should go in the references / "Works discussed" section,
            # not in the body prose. If neither the article nor the journal share
            # a disease term, skip it. The reviewer already said: "only suggest
            # if you are sure the journal matches the study."
            is_journal = target.get("section") == "IJCCD"
            if is_journal:
                src_diseases = keyword_scan.article_diseases(article)
                tgt_blob = f"{target.get('h1') or ''} {target.get('title_clean') or ''}".lower()
                disease_hit = any(d in tgt_blob for d in src_diseases)
                if not disease_hit:
                    continue  # no shared disease term: skip this journal target

            named_ok, name_note = rules.contributor_named(target, chunk["text"])
            if not named_ok:
                continue

            # v3: relaxed hard gate (was 0.70, now 0.55).
            if cand["raw_cosine"] < settings.COSINE_HARD_MIN:
                # Keyword scan, title similarity or an awareness/conference
                # disease match can rescue it.
                kw = kw_scores.get(target["url"], 0.0)
                ts = title_sims.get(target["url"], 0.0)
                if kw < 0.20 and ts < settings.TITLE_SIMILARITY_MIN \
                        and target["url"] not in aware:
                    continue

            best = anchor_mod.select(target, chunk["text"], store.guide)
            if not best or not rules.anchor_word_count_ok(best["anchor"]):
                continue

            if best["match_type"] == "Needs insertion" \
                    and cand["raw_cosine"] < settings.COSINE_INSERTION_MIN:
                kw = kw_scores.get(target["url"], 0.0)
                if kw < 0.25:
                    continue

            kw = kw_scores.get(target["url"], 0.0)
            ts = title_sims.get(target["url"], 0.0)
            aw_floor, aw_reason = aware.get(target["url"], (0.0, ""))

            score = rules.final_score(
                cand["semantic_score"], cand["lexical_score"], best["anchor_score"],
                keyword_score=kw, title_sim=ts, extra_floor=aw_floor,
                deorphan=deorphan, inbound=target.get("inbound_link_count", 0),
                synthetic=not cand["chunk"].get("placement_ok", True),
                evidence=rules.keyword_evidence(
                    best["tier"], best["match_type"], best["anchor"]))

            keep, conf_note, force_lower = rules.conference_ok(target, score)
            if not keep:
                continue

            b = rules.band(score)
            if b is None:
                continue
            if force_lower:
                b = "Lower"

            level, why, basis = rules.overlap(article, target, best["anchor"], cmap)

            # v3: LLM rewrite for "Needs insertion" rows.
            llm_text = None
            if best["match_type"] == "Needs insertion":
                llm_text, was_rewritten = llm_rewrite.rewrite(
                    chunk["text"], best["anchor"],
                    target.get("h1") or target.get("title_clean") or "",
                    target["url"])
                if not was_rewritten:
                    llm_text = None

            kw_note = f"Keyword match: {kw:.0%}" if kw >= 0.10 else ""
            ts_note = f"Same-topic match: {ts:.0%}" if ts >= settings.TITLE_SIMILARITY_MIN else ""
            if aw_reason:
                ts_note = ". ".join(x for x in (ts_note, aw_reason) if x)

            # v5b: detect existing links in this sentence/block.
            existing_in_block = chunk.get("existing_links", [])
            link_warn = ""
            if existing_in_block:
                link_urls = [u for u, _a in existing_in_block]
                link_warn = f"This paragraph already has {len(link_urls)} link(s): {', '.join(link_urls[:3])}"

            group.append({
                "block_index": chunk["block_index"],
                "existing_sentence": chunk["text"],
                "modified_sentence": rules.modified_sentence(
                    chunk["text"], best["span"], best["anchor"],
                    target["url"], best["match_type"], llm_rewrite=llm_text),
                "anchor": best["anchor"],
                "target_url": target["url"],
                "target_title": target.get("h1") or target.get("title_clean") or "",
                "section": target.get("section", ""),
                "relevance": b,
                "match_type": best["match_type"],
                "overlap_level": level,
                "overlap_why": why,
                "overlap_basis": basis,
                "score": score,
                "extra_note": ". ".join(x for x in (name_note, conf_note, link_warn) if x),
                "keyword_note": kw_note,
                "title_sim_note": ts_note,
                "is_topical": bool(kw >= 0.15 or ts >= settings.TITLE_SIMILARITY_MIN
                                   or aw_floor > 0 or cand["raw_cosine"] >= 0.62),
                "is_journal_target": is_journal,
                "review_context": _review_context(
                    article, target, cand, kw, ts, best["match_type"]),
                "_page": target,
            })

        group.sort(key=lambda r: -r["score"])
        for i, r in enumerate(group):
            r["notes"] = _recommend_note(r, r.pop("_page"), is_best=(i == 0))
        rows.extend(group)

    # v3: KEYWORD-SCAN RESCUE. Pages the keyword scanner or title similarity
    # surfaced but embedding retrieval never evaluated get a second chance.
    # Pick the best placeable chunk for each rescued page.
    rescued_urls = (set(kw_scores.keys()) | set(title_sims.keys())) - evaluated_targets - already
    if rescued_urls and placeable:
        # Embed all placeable chunks once.
        chunk_texts = [chunker.embed_text(c) for c in placeable]
        chunk_vecs = embedder.embed_passages(chunk_texts)

        for url in rescued_urls:
            target = store.page(url)
            if target is None or not _sections_filter(target, allowed_sections):
                continue
            if not rules.url_ok(target["url"]):
                continue
            if rules.already_linked_in_body(article, target["url"]):
                continue

            kw = kw_scores.get(url, 0.0)
            ts = title_sims.get(url, 0.0)
            aw_floor, aw_reason = aware.get(url, (0.0, ""))
            if kw < 0.15 and ts < settings.TITLE_SIMILARITY_MIN and aw_floor <= 0:
                continue

            # Find the best chunk for this target by trying anchor matching.
            best_row = None
            for ci, chunk in enumerate(placeable):
                named_ok, name_note = rules.contributor_named(target, chunk["text"])
                if not named_ok:
                    continue
                best = anchor_mod.select(target, chunk["text"], store.guide)
                if not best or not rules.anchor_word_count_ok(best["anchor"]):
                    continue

                # Compute a lightweight cosine score for this chunk-target pair.
                t_parts = [target.get("h1") or "", target.get("title_clean") or "",
                           target.get("meta_description") or ""]
                t_text = ". ".join(p for p in t_parts if p)
                if t_text:
                    t_vec = embedder.embed_queries([t_text])
                    raw_cos = float(chunk_vecs[ci] @ t_vec[0])
                else:
                    raw_cos = 0.0

                sem = max(0.0, min(1.0, (raw_cos - settings.COSINE_NOISE_FLOOR)
                                   / (settings.COSINE_SIGNAL_CEIL - settings.COSINE_NOISE_FLOOR)))

                score = rules.final_score(
                    sem, 0.0, best["anchor_score"],
                    keyword_score=kw, title_sim=ts, extra_floor=aw_floor,
                    deorphan=deorphan, inbound=target.get("inbound_link_count", 0),
                    evidence=rules.keyword_evidence(
                        best["tier"], best["match_type"], best["anchor"]))

                b = rules.band(score)
                if b is None:
                    continue

                if best_row is None or score > best_row["score"]:
                    # LLM rewrite for needs-insertion.
                    llm_text = None
                    if best["match_type"] == "Needs insertion":
                        llm_text, was_rewritten = llm_rewrite.rewrite(
                            chunk["text"], best["anchor"],
                            target.get("h1") or target.get("title_clean") or "",
                            target["url"])
                        if not was_rewritten:
                            llm_text = None

                    keep, conf_note, force_lower = rules.conference_ok(target, score)
                    if not keep:
                        continue
                    if force_lower:
                        b = "Lower"

                                       level, why, basis = rules.overlap(article, target, best["anchor"], cmap)
                    kw_note = f"Keyword match: {kw:.0%}" if kw >= 0.10 else ""
                    ts_note = (f"Same-topic match: {ts:.0%}"
                               if ts >= settings.TITLE_SIMILARITY_MIN else "")

                    best_row = {
                        "block_index": chunk["block_index"],
                        "existing_sentence": chunk["text"],
                        "modified_sentence": rules.modified_sentence(
                            chunk["text"], best["span"], best["anchor"],
                            target["url"], best["match_type"], llm_rewrite=llm_text),
                        "anchor": best["anchor"],
                        "target_url": target["url"],
                        "target_title": target.get("h1") or target.get("title_clean") or "",
                        "section": target.get("section", ""),
                        "relevance": b,
                        "match_type": best["match_type"],
                        "overlap_level": level,
                        "overlap_why": why,
                        "overlap_basis": basis,
                        "score": score,
                        "extra_note": ". ".join(x for x in (name_note, conf_note) if x),
                        "keyword_note": kw_note,
                        "title_sim_note": ts_note,
                        "is_topical": bool(kw >= 0.15
                                           or ts >= settings.TITLE_SIMILARITY_MIN
                                           or aw_floor > 0),
                        "notes": "",
                    }

            if best_row:
                best_row["notes"] = _recommend_note(
                    best_row, target, is_best=False)
                rows.append(best_row)

    rows = rules.enforce_caps(rows)
    if not show_lower:
        rows = [r for r in rows if r["relevance"] in ("High", "Medium")]
    return rows


def links_to_receive(article: dict, store: retrieval.Store,
                     allowed_sections: set[str] | None = None,
                     show_lower: bool = True) -> list[dict]:
    """Which existing pages should add a link pointing to this article.

    v3: also searches via keyword scan and title similarity.
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

    # v3: keyword scan and title similarity to find pages that should link here.
    body_texts = getattr(store, "body_texts", {}) or {}
    cmap = getattr(store, "cannibalization", {}) or {}
    article_terms = keyword_scan.extract_terms(article)

    # v4 (review item #3): pages that ALREADY link to this article must not be
    # suggested as inbound opportunities. The v3 code only checked this for
    # published articles inside the loop, which let an already-linking page
    # through whenever its chunk came from the keyword scanner instead of
    # embedding retrieval.
    already_inbound = {
        u for u, p in store.pages.items()
        if article["url"] in set(p.get("body_internal_links") or [])
    }
    recv_exclude = {article["url"]} | already_inbound

    kw_scores = keyword_scan.scan_pages(article_terms, body_texts, recv_exclude)

    # v5 BIDIRECTIONAL SCAN: also check which pages' own disease terms appear
    # in the source article. This catches "Nepal's Cancer Burden mentions
    # 'alcohol consumption' → should link to the alcohol article" which the
    # forward scan misses because it only looks for the source's terms in
    # other pages, not other pages' terms in the source.
    source_body = " ".join(
        (b.get("text") if isinstance(b, dict) else getattr(b, "text", ""))
        for b in article.get("blocks", [])
    )
    reverse_scores = keyword_scan.reverse_scan(
        source_body, store.pages, body_texts, recv_exclude)
    # Merge: take the max of forward and reverse for each URL.
    for url, score in reverse_scores.items():
        kw_scores[url] = max(kw_scores.get(url, 0.0), score)
    title_sims = keyword_scan.title_similarity(article, store.pages, recv_exclude)

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

    # v3: merge keyword-scan and title-sim pages that embedding missed.
    cand_urls = {c["url"] for c in cands}
    for url in (set(kw_scores.keys()) | set(title_sims.keys())) - cand_urls:
        # Find the best chunk from this page.
        chunk_ids = store.by_url.get(url, [])
        if not chunk_ids:
            continue
        best_chunk = None
        for ci in chunk_ids:
            c = store.chunks[ci]
            if c.get("placement_ok", True):
                if best_chunk is None or ci < best_chunk["chunk_index"]:
                    best_chunk = {
                        "chunk_index": ci, "chunk": c, "url": url,
                        "rrf": 0.0, "raw_cosine": 0.0,
                        "semantic_score": 0.0, "raw_bm25": 0.0,
                        "lexical_score": 0.0,
                    }
        if best_chunk:
            cands.append(best_chunk)

    rows = []
    for cand in cands:
        source = store.page(cand["url"])
        chunk = cand["chunk"]
        if source is None or not _sections_filter(source, allowed_sections):
            continue
        # v4: IJCCD articles are published research. Nobody is going back into a
        # journal paper to insert a link to a TCN article. Skip them entirely on
        # the receive side.
        if source.get("section") == "IJCCD":
            continue
        # v3: relaxed hard gate; keyword/title signals can rescue.
        kw = kw_scores.get(cand["url"], 0.0)
        ts = title_sims.get(cand["url"], 0.0)
        if cand["raw_cosine"] < settings.COSINE_HARD_MIN and kw < 0.20 \
                and ts < settings.TITLE_SIMILARITY_MIN:
            continue
        if not chunk.get("placement_ok", True):
            continue
        if article.get("is_draft"):
            note_pub = ("Article not yet published; verify no existing link once "
                        "the URL is live")
        else:
            if rules.already_linked_in_body(source, article["url"]):
                continue
            note_pub = ""

        best = anchor_mod.select(article, chunk["text"], store.guide)
        if not best or not rules.anchor_word_count_ok(best["anchor"]):
            continue
        if best["match_type"] == "Needs insertion" \
                and cand["raw_cosine"] < settings.COSINE_INSERTION_MIN \
                and kw < 0.25:
            continue

        score = rules.final_score(
            cand["semantic_score"], cand["lexical_score"], best["anchor_score"],
            keyword_score=kw, title_sim=ts,
            evidence=rules.keyword_evidence(
                best["tier"], best["match_type"], best["anchor"]))
        b = rules.band(score)
        if b is None:
            continue
        level, why, basis = rules.overlap(source, article, best["anchor"], cmap)

        # LLM rewrite for receive-side needs-insertion.
        llm_text = None
        if best["match_type"] == "Needs insertion":
            llm_text, was_rewritten = llm_rewrite.rewrite(
                chunk["text"], best["anchor"],
                article.get("h1") or article.get("title_clean") or "",
                article["url"])
            if not was_rewritten:
                llm_text = None

        kw_note = f"Keyword match: {kw:.0%}" if kw >= 0.10 else ""
        ts_note = f"Same-topic: {ts:.0%}" if ts >= settings.TITLE_SIMILARITY_MIN else ""
        notes = ". ".join(x for x in (note_pub, rules.crowding_note(source),
                                       kw_note, ts_note) if x)
        rows.append({
            "source_url": source["url"],
            "source_title": source.get("h1") or source.get("title_clean") or "",
            "section": source.get("section", ""),
            "existing_sentence": chunk["text"],
            "modified_sentence": rules.modified_sentence(
                chunk["text"], best["span"], best["anchor"],
                article["url"], best["match_type"], llm_rewrite=llm_text),
            "anchor": best["anchor"],
            "relevance": b,
            "match_type": best["match_type"],
            "overlap_level": level,
            "overlap_why": why,
            "overlap_basis": basis,
            "score": score,
            "is_topical": bool(kw >= 0.15 or ts >= settings.TITLE_SIMILARITY_MIN
                               or cand["raw_cosine"] >= 0.62),
            "review_context": _review_context(
                article, source, cand, kw, ts, best["match_type"]),
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

    llm_ok, llm_provider = llm_rewrite.is_available()

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
            "body_texts": store.manifest.get("body_texts_stored", 0),
            "age_days": round(retrieval.index_age_days(store), 1),
        },
        "llm": {"available": llm_ok, "provider": llm_provider},
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
