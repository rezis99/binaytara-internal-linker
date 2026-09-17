"""The SOP rule engine (R1 to R11) plus scoring.

Each rule is a separate, individually testable function. The block classifier has
already enforced R1 to R4 by marking blocks ineligible, so only eligible chunks
ever reach here; the rules below are the candidate-level filters.
"""
from __future__ import annotations

import re
from datetime import date

from config import settings
from config import url_rules as ur
from engine import cannibalization, conference


# ---------------------------------------------------------------- R8
def anchor_word_count_ok(anchor: str) -> bool:
    n = len((anchor or "").split())
    return settings.ANCHOR_MIN_WORDS <= n <= settings.ANCHOR_MAX_WORDS


# ---------------------------------------------------------------- R9
def url_ok(url: str) -> bool:
    """Absolute https, main domain, no query string (SOP Rules 5 and 6)."""
    if not url or not url.startswith("https://"):
        return False
    if "?" in url or "#" in url:
        return False
    return ur.is_allowed(url)


# ---------------------------------------------------------------- R7
def already_linked_in_body(source_page: dict, target_url: str) -> bool:
    """Navigation, breadcrumb and footer links deliberately do NOT count: a nav
    link to X must not block a contextual body link to X."""
    return target_url in set(source_page.get("body_internal_links") or [])


# ---------------------------------------------------------------- R10
def contributor_named(target_page: dict, paragraph_text: str) -> tuple[bool, str]:
    """A contributor page is only suggested when the person is named in the
    paragraph. Surname-only forms additionally require an adjacent title or
    credential, because a bare surname such as 'Shah' over-matches badly here."""
    if not ur.is_contributor(target_page["url"]):
        return True, ""
    text = paragraph_text or ""
    for variant in target_page.get("person_names") or []:
        if variant.startswith("SURNAME_ONLY::"):
            surname = variant.split("::", 1)[1]
            pattern = (r"\b(?:Dr\.?|Prof\.?|Professor)\s+" + re.escape(surname) +
                       r"\b|\b" + re.escape(surname) +
                       r"\b\s*,?\s*(?:MD|PhD|DO|MPH|RN|DNB|MBBS)\b")
            if re.search(pattern, text, re.I):
                return True, f"Contributor '{surname}' named with a title in this paragraph"
            continue
        if re.search(r"\b" + re.escape(variant) + r"\b", text, re.I):
            return True, f"Contributor '{variant}' named in this paragraph"
    return False, ""


# ---------------------------------------------------------------- R11
def conference_ok(target_page: dict, score: float,
                  today: date | None = None) -> tuple[bool, str, bool]:
    """Return (keep, note, force_lower_band)."""
    if target_page.get("section") != "Conference":
        return True, "", False
    active, note = conference.is_active(target_page, today)
    if not active:
        return False, note, False
    if score < settings.BAND_MEDIUM:
        return False, note, False
    if score < settings.CONFERENCE_MIN_SCORE:
        # Not dropped silently: the writer would otherwise wonder why an
        # obviously relevant conference never appears.
        return True, (f"{note}. Below the {settings.CONFERENCE_MIN_SCORE} conference "
                      "confidence minimum; verify relevance and dates before linking"), True
    return True, note, False


# ---------------------------------------------------------------- scoring
def final_score(semantic: float, lexical: float, anchor_score: float,
                deorphan: bool = False, inbound: int = 0,
                synthetic: bool = False) -> float:
    s = (settings.W_SEMANTIC * semantic
         + settings.W_LEXICAL * lexical
         + settings.W_ANCHOR * anchor_score)
    if synthetic:
        s *= settings.SYNTHETIC_PENALTY
    if deorphan:
        s += settings.DEORPHAN_BONUS * (
            1 - min(inbound, settings.DEORPHAN_CAP) / settings.DEORPHAN_CAP
        )
    return round(min(s, 1.0), 4)


def band(score: float) -> str | None:
    if score >= settings.BAND_HIGH:
        return "High"
    if score >= settings.BAND_MEDIUM:
        return "Medium"
    if score >= settings.BAND_MIN:
        return "Lower"
    return None


def benchmark(word_count: int) -> str:
    for ceiling, label in settings.LINK_BENCHMARK:
        if word_count < ceiling:
            return label
    return settings.LINK_BENCHMARK[-1][1]


# ---------------------------------------------------------------- R5, R6
def enforce_caps(rows: list[dict]) -> list[dict]:
    """R6: each target page at most once per article, keeping the best placement.
    R5: at most two suggestions per paragraph, keeping the highest scoring."""
    rows = sorted(rows, key=lambda r: -r["score"])
    best_for_target: dict[str, dict] = {}
    for r in rows:
        prev = best_for_target.get(r["target_url"])
        if prev is None or r["score"] > prev["score"]:
            best_for_target[r["target_url"]] = r
    kept = list(best_for_target.values())

    per_block: dict[int, int] = {}
    out = []
    for r in sorted(kept, key=lambda r: -r["score"]):
        b = r["block_index"]
        if per_block.get(b, 0) >= settings.MAX_PER_PARAGRAPH:
            continue
        per_block[b] = per_block.get(b, 0) + 1
        out.append(r)
    return sorted(out, key=lambda r: (r["block_index"], -r["score"]))


def modified_sentence(text: str, span, anchor: str, url: str, match_type: str) -> str:
    """Markdown link inserted in place, or an instruction when the phrase is
    absent. Phase 2 replaces the instruction with an LLM rewrite."""
    if match_type == "Needs insertion" or not span:
        return (f'Writer to incorporate the phrase "{anchor}" naturally into this '
                f"sentence, then link it to {url}")
    start, end = span
    return f"{text[:start]}[{text[start:end]}]({url}){text[end:]}"


def crowding_note(page: dict) -> str:
    n = len(page.get("body_internal_links") or [])
    if n >= settings.CROWDED_PAGE_LINKS:
        return (f"This page already has {n} internal links; only add from a highly "
                "relevant anchor")
    return ""


def overlap(source_page: dict, target_page: dict, anchor: str) -> tuple[str, str]:
    return cannibalization.assess(source_page, target_page, anchor)
