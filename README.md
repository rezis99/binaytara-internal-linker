# Binaytara Internal Link Recommender

Suggests internal links for binaytara.org articles, enforcing the Content
Publishing SOP. Give it a live URL or a `.docx` draft; get back where to place
links out, which existing pages should link in, and a formatted Excel workbook.

**Start here: [SETUP.md](SETUP.md)** — full install and operation guide.

## Quick start

```bash
pip install -r requirements.txt
python tests/test_rules.py        # 52 tests, all must pass
streamlit run app.py              # ships with a 368-page sample index
python -m indexer.build_index     # build the full 1,680-page index
```

## How it works

Offline (GitHub Actions, Mon and Wed 06:00 NPT): sitemap → crawl → extract →
chunk → embed → FAISS + BM25 → commit artifacts.

Online (Streamlit): load prebuilt index → parse article → retrieve candidates
(dense + lexical, fused with RRF) → select anchors → apply SOP rules → score → export.

The split is what lets the whole thing run free: the app never crawls.

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

FAQ sections remain eligible. Subdomains and `binayfoundation.org` are excluded.

## Layout

```
app.py                  Streamlit front end
config/                 settings, URL rules, selectors, synonym map
indexer/                sitemap, crawler, extractor, blocks, chunker, embedder, build
engine/                 input parsing, retrieval, anchors, rules, overlap, orchestrator
output/excel_writer.py  formatted workbook
tests/test_rules.py     52 unit tests
```

Every threshold lives in `config/settings.py`. Nothing else hard-codes one.
