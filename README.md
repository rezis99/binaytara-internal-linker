# Binaytara Internal Link Recommender (v3)

Suggests internal links for binaytara.org articles, enforcing the Content
Publishing SOP. Give it a live URL or a `.docx` draft; get back where to place
links out, which existing pages should link in, and a formatted Excel workbook.

**Start here: [SETUP.md](SETUP.md)** for install and operation guide.

## What changed in v3

v3 fixes the three root causes identified in the 6-article audit (23% approval
rate, 11 of 48 suggestions usable):

| Fix | What it solves |
|---|---|
| Canonical collision fix | URL/title mismatches in 5 of 6 files; ghost data (wrong page's content stored under another page's URL) |
| Keyword scan | Missed "alcohol" ↔ "stomach cancer" and 20+ other obvious topical links the embedding model compressed away |
| Same-topic detection | Two stomach cancer articles never finding each other despite being the most obvious link pair on the site |
| Threshold recalibration | v2 overcorrected: same article went from 26 suggestions to 2. v3 targets 8 to 15 per article |
| Body text storage | Pages' body text stored at index time so keyword scan works at query time without re-crawling |
| LLM sentence rewriting | "Needs insertion" rows now propose a complete rewritten sentence instead of a manual editing instruction |

## Quick start

```bash
pip install -r requirements.txt
python tests/test_rules.py        # 70+ tests, all must pass
streamlit run app.py              # ships with whatever index is in data/
python -m indexer.build_index     # rebuild the full 1,680-page index with v3 fixes
```

## How it works

Offline (GitHub Actions, Mon and Wed 06:00 NPT): sitemap → crawl → extract →
chunk → embed → FAISS + BM25 → store body texts → commit artifacts.

Online (Streamlit): load prebuilt index → parse article → retrieve candidates
(dense + lexical + keyword scan + title similarity, fused) → select anchors →
apply SOP rules → score → LLM rewrite → export.

## SOP rules enforced

| Rule | Behaviour |
|---|---|
| R1 | No suggestion in the first paragraph |
| R2 | None in Key Takeaways |
| R3 | None in references / "Works discussed" |
| R4 | None inside a direct quote |
| R5 | Max 2 per paragraph |
| R6 | Each target page suggested once per article |
| R7 | Never suggest a target already linked in the body. Navigation links do not count |
| R8 | Anchor text 2 to 5 words |
| R9 | Absolute https main-domain URLs, no query strings |
| R10 | Contributor pages only when the person is named |
| R11 | Conference pages only when the event has not ended and confidence is high |

## v3 retrieval pipeline

```
Source article
  │
  ├── Embedding retrieval (FAISS + BM25, fused with RRF)
  │     → candidates by semantic and lexical similarity
  │
  ├── Keyword scan (v3)
  │     → extract disease terms from H1, headings, body
  │     → grep every page's stored body text for those terms
  │     → pages mentioning the article's core terms get a score floor
  │
  └── Title similarity (v3)
        → Jaccard on H1/title content words
        → same-topic articles always surface as candidates
  │
  Merge → anchor selection → SOP rules → scoring → LLM rewrite → output
```

## LLM rewriting (v3)

"Needs insertion" rows previously gave writers an instruction like "incorporate
the phrase X naturally." The audit found this unusable. v3 sends the original
sentence and anchor to a free LLM API and returns a complete proposed sentence.

Providers (checked in order):
1. Groq free tier (set `GROQ_API_KEY` in Streamlit secrets)
2. HuggingFace Inference API (set `HF_TOKEN` in Streamlit secrets)
3. Disabled: falls back to the manual instruction

Validation: the anchor must appear verbatim, no new medical claims, sentence
length within 0.5x to 2.5x of the original.

## Layout

```
app.py                  Streamlit front end
config/                 settings, URL rules, selectors, synonym map
indexer/                sitemap, crawler, extractor, blocks, chunker, embedder, build
engine/                 input parsing, retrieval, keyword scan, anchors, rules, LLM, orchestrator
output/excel_writer.py  formatted workbook
tests/test_rules.py     70+ unit tests
tests/test_end_to_end.py  integration tests
```

Every threshold lives in `config/settings.py`. Nothing else hard-codes one.
