"""Keyword scanner and same-topic detector (v3).

Two independent retrieval signals that supplement the embedding pipeline:

1. KEYWORD SCAN: extract core disease terms and risk factors from the source
   article, then grep every page's stored body text for those phrases. This is
   what catches "alcohol" <> "stomach cancer" and every other obvious topical
   link the embedding model misses because it compresses domain vocabulary.

2. SAME-TOPIC DETECTION: pairwise Jaccard similarity on H1/title content words.
   Two stomach cancer articles always find each other, regardless of what the
   embedding says about individual paragraphs.

Both return candidate page URLs with scores that the orchestrator merges into
the main retrieval pipeline.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from config import selectors as sel
from config import settings

# Words too common to be useful as keyword-scan terms.
_NOISE = {
    "the", "a", "an", "and", "or", "but", "of", "in", "on", "at", "to", "for",
    "with", "by", "from", "as", "is", "are", "was", "were", "be", "been", "it",
    "its", "this", "that", "these", "those", "their", "there", "how", "what",
    "why", "when", "who", "which", "can", "could", "will", "would", "should",
    "has", "have", "had", "do", "does", "did", "you", "your", "we", "our",
    "not", "all", "more", "most", "some", "also", "than", "very", "into",
    "about", "between", "after", "before", "during", "may", "might", "each",
    "been", "being", "such", "they", "them", "then", "here", "both", "other",
    "one", "two", "three", "many", "much", "new", "first", "last", "long",
    "over", "only", "own", "same", "so", "no", "just", "now", "any",
    # Domain noise: too frequent in an oncology corpus to be discriminating.
    "cancer", "patient", "patients", "treatment", "study", "risk", "health",
    "disease", "clinical", "research", "data", "results", "rate", "rates",
    "care", "medical", "diagnosis", "therapy", "found", "associated", "cases",
    "group", "years", "year", "including", "based", "using", "used", "among",
    "article", "news", "binaytara",
}


@lru_cache(maxsize=1)
def _synonym_map() -> dict[str, list[str]]:
    path = settings.ROOT / "config" / "oncology_synonyms.json"
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return {}


def _content_words(text: str) -> set[str]:
    """Lowercase content words, noise removed."""
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if w not in _NOISE and len(w) >= settings.KEYWORD_MIN_TERM_LEN}


def _extract_ngrams(text: str, min_n: int = 2, max_n: int = 4) -> list[str]:
    """Content-word n-grams from a text. Returns lowercased phrases."""
    words = re.findall(r"[a-z][a-z0-9\-']*", (text or "").lower())
    out = []
    for n in range(min_n, max_n + 1):
        for i in range(len(words) - n + 1):
            gram = words[i:i + n]
            # At least one word must be a content word (not noise).
            if any(w not in _NOISE and len(w) >= settings.KEYWORD_MIN_TERM_LEN
                   for w in gram):
                phrase = " ".join(gram)
                out.append(phrase)
    return out


def extract_terms(article: dict) -> list[str]:
    """Extract core disease terms and risk factors from an article.

    Sources, in priority order:
    1. H1 n-grams (strongest signal: the article is *about* these)
    2. Section heading n-grams
    3. Body text n-grams that appear in the synonym map (known medical terms)
    4. High-frequency body n-grams

    Returns deduplicated terms, capped at KEYWORD_MAX_TERMS.
    """
    terms: list[str] = []
    seen: set[str] = set()

    def _add(phrase: str) -> None:
        key = phrase.strip().lower()
        if key and key not in seen:
            seen.add(key)
            terms.append(key)

    # 1. H1 terms
    h1 = article.get("h1") or ""
    for gram in _extract_ngrams(h1, 2, 4):
        _add(gram)
    # Also add individual content words from H1 if they are medical terms.
    for w in _content_words(h1):
        if w in _synonym_map() or len(w) >= 6:
            _add(w)

    # 2. Section headings from blocks
    for block in article.get("blocks", []):
        kind = block.get("kind") if isinstance(block, dict) else getattr(block, "kind", "")
        if kind in ("h2", "h3", "h4"):
            text = block.get("text") if isinstance(block, dict) else getattr(block, "text", "")
            for gram in _extract_ngrams(text, 2, 3):
                _add(gram)

    # 3. Synonym map matches from body text
    body = " ".join(
        (b.get("text") if isinstance(b, dict) else getattr(b, "text", ""))
        for b in article.get("blocks", [])
    ).lower()
    for canonical, variants in _synonym_map().items():
        all_forms = [canonical.lower()] + [v.lower() for v in variants]
        for form in all_forms:
            if form in body:
                _add(form)
                _add(canonical.lower())
                break

    # 4. Frequent body n-grams (counted, top ones added)
    body_grams = _extract_ngrams(body, 2, 3)
    freq: dict[str, int] = {}
    for g in body_grams:
        if g not in seen:
            freq[g] = freq.get(g, 0) + 1
    for gram, count in sorted(freq.items(), key=lambda x: -x[1])[:10]:
        if count >= 2:
            _add(gram)

    return terms[:settings.KEYWORD_MAX_TERMS]


def scan_pages(terms: list[str], body_texts: dict[str, str],
               exclude_urls: set[str]) -> dict[str, float]:
    """For each page, count how many of the source article's terms appear in its
    body text. Returns {url: normalised_match_score}.

    The score is (matched_terms / total_terms), so a page that mentions 5 of 10
    terms scores 0.50. This is independent of the embedding and captures the
    exact connections embeddings miss.
    """
    if not terms or not body_texts:
        return {}

    hits: dict[str, int] = {}
    for url, text in body_texts.items():
        if url in exclude_urls:
            continue
        count = 0
        for term in terms:
            if term in text:
                count += 1
        if count > 0:
            hits[url] = count

    total = len(terms)
    return {url: min(count / total, 1.0) for url, count in hits.items()}


def _title_words(text: str) -> set[str]:
    """Content words for title comparison. Uses a LIGHTER noise filter than body
    scanning: keeps domain-specific terms like 'cancer' that are generic in body
    text but discriminating in a title. Without this, 'Stomach Cancer Symptoms'
    and 'Stomach Cancer Treatment' cannot find each other because the shared
    word 'cancer' is filtered out.
    """
    # Only stop words and very short words, not domain terms.
    _title_noise = {
        "the", "a", "an", "and", "or", "but", "of", "in", "on", "at", "to",
        "for", "with", "by", "from", "as", "is", "are", "was", "were", "be",
        "it", "its", "this", "that", "how", "what", "why", "when", "who",
        "which", "can", "will", "has", "have", "had", "do", "does", "not",
        "all", "more", "most", "also", "new", "may",
    }
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if w not in _title_noise and len(w) >= 3}


def title_similarity(source: dict, pages: dict,
                     exclude_urls: set[str]) -> dict[str, float]:
    """Jaccard similarity between the source article's H1/title and every
    indexed page's H1/title. Returns pages above TITLE_SIMILARITY_MIN.

    This catches the "two stomach cancer articles never finding each other"
    failure: their titles share enough content words that Jaccard alone
    surfaces them.
    """
    src_words = _title_words(source.get("h1") or "") | \
        _title_words(source.get("title_clean") or "")
    if not src_words:
        return {}

    results: dict[str, float] = {}
    for url, page in pages.items():
        if url in exclude_urls:
            continue
        tgt_words = _title_words(page.get("h1") or "") | \
            _title_words(page.get("title_clean") or "")
        if not tgt_words:
            continue
        inter = len(src_words & tgt_words)
        jacc = inter / len(src_words | tgt_words)
        # Overlap coefficient catches the case where two titles share a core
        # phrase ("stomach cancer") but each has several unique words. Without
        # it, Jaccard underweights the shared core.
        overlap = inter / min(len(src_words), len(tgt_words)) if min(len(src_words), len(tgt_words)) > 0 else 0.0
        sim = max(jacc, overlap * 0.85)  # scale overlap; catches shared-core titles
        if sim >= settings.TITLE_SIMILARITY_MIN:
            results[url] = sim

    return results
