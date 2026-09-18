# Binaytara Internal Link Recommender (v4)

Suggests internal links for binaytara.org articles, enforcing the Content
Publishing SOP. Give it a live URL or a `.docx` draft; get back where to place
links out, which existing pages should link in, and a formatted Excel workbook.

**Start here: [SETUP.md](SETUP.md)** for install and operation guide.

## What the section filters mean

The sidebar lets you toggle which page types the tool considers:

| Filter | What it covers | Example URLs |
|---|---|---|
| TCN | The Cancer News articles (the main editorial content) | `/cancernews/article/stomach-cancer-101` |
| IJCCD | International Journal of Cancer Care and Delivery research papers | `/journal/article/129490-racial-disparities-...` |
| Blog | Organizational news and announcements | `/news/education/the-top-oncology-conferences-...` |
| Conference | Individual conference event pages | `/projects/conferences/2026-seattle-lung-cancer-conference` |
| Project | Project hubs: OncoBlast, the conferences index page, etc. | `/projects/oncoblast`, `/projects/conferences` |
| Static | Evergreen pages: about, research grants, contact | `/about`, `/research-grants` |
| Contributor | TCN author profile pages | `/cancernews/contributors/dr-jane-smith` |

Removing a tag excludes that entire category from all suggestions.

### How IJCCD articles are handled

IJCCD pages are published research papers. They receive special treatment:

**Receive side (which pages should link TO this article):** IJCCD pages are
excluded entirely. Nobody edits a journal paper to insert a link to a TCN
article.

**Give side (where to place links FROM this article):** An IJCCD article is only
suggested when its core disease matches the source article's disease terms. When
suggested, the Notes column advises placing the link in a references or "Works
discussed" section rather than in body prose.

## What changed in v4

v4 responds to the September 2026 writer implementation review. Each item below
maps to a numbered issue in that document.

| Review item | Fix in v4 |
|---|---|
| #1 Ghost data ("Maasai Girls" in kidney cancer rows) | Root cause found: the CMS serves a foreign `<h1>` on ~11% of TCN articles. The tool now detects this and falls back to `<title>`, and lists every affected URL in the manifest |
| #2 URL/title mismatches | Same root cause, same fix. The mismatch list is a CMS defect report for the dev team |
| #3 Receive suggests pages that already link here | Inbound links are now computed across the whole index and excluded before scoring |
| #4 Receive misses pages with the keyword in body text | The keyword scanner needs `body_texts.json`, which only exists in a v3+ index. Confirm the sidebar reads "Index version: 3" or higher |
| #5 Generic anchor blocklist | "all cancer", "cancer awareness", "risk factors" and 30+ more blocked, plus a geographic blocklist |
| #6 "[Disease] Awareness Month" pages not detected | Disease-term matching surfaces the awareness page for any disease the article is about |
| #7 Conference coverage cross-linking | Conference recaps mentioning the article's disease now match on the disease term |
| #8 Orphan rescue mixed with topical | Split into separate tabs ("Links to Give: topical" vs "Links to Give: other") and separate Excel sheets |
| #9 Output formatting | Decision columns first: Use?, Relevance, Match Type, Keyword Competition |
| IJCCD receive spam | Journal pages are fully excluded from the Receive table |
| IJCCD give accuracy | Journal articles only suggested when their disease term matches; marked for the references section |
| Batch mode one-URL bug | One top-level tab per article; fixed the Streamlit key collision |

## Maintaining the Semrush cannibalization data

v4 uses a Semrush organic positions export to detect pages that compete for the
same search queries. This replaces the v3 title heuristic with measured data.

### How to update it

You only need to do this when you want fresh competition data, typically once a
month or after publishing a batch of new articles.

**Step 1: Export from Semrush.**
Go to Semrush → Organic Research → enter `binaytara.org` → Positions tab →
click "Export" (top right) → choose XLSX → save the file.

**Step 2: Generate the map.**
Open a terminal (or use the GitHub Codespace) and run:

```bash
python -m engine.cannibalization_data path/to/semrush-export.xlsx
```

This reads the export and writes `data/cannibalization.json` (~135 KB).

**Step 3: Commit to GitHub.**

```bash
git add data/cannibalization.json
git commit -m "Update cannibalization map from September 2026 Semrush export"
git push
```

Streamlit will pick up the new file on the next reboot or deploy.

### What happens without it

The map is optional. When `data/cannibalization.json` is missing, the tool falls
back to the v3 title heuristic and says so in every competition cell: "None
(title heuristic)" instead of "None (query data)". No suggestions are suppressed
either way; the map only changes how competition is labelled.

### What about new articles that are not ranking yet?

New articles need a few weeks to accumulate ranking data in Semrush. Until they
appear in the export, the tool uses the title heuristic for those pages and
labels it accordingly. Once they show up in the next export, the real query data
takes over automatically.

### How the tool decides which page to suggest from a cannibalized pair

The tool does NOT suppress suggestions between competing pages. It flags them
and shows exactly which queries they share. A writer can then decide: "These two
pages both rank for 'ivermectin for cancer.' I will link FROM the weaker page TO
the stronger page with that anchor, to consolidate the signal." The flag is the
decision aid.

## The CMS H1 defect

Roughly 11% of TCN articles (29 of 263, measured September 2026) serve an `<h1>`
belonging to a completely different article while the `<title>` and canonical
tag are correct. Example:

```
URL:     /cancernews/article/kidney-cancer-dual-io-hif2a-tki-free-intervals
<title>  Kidney Cancer in 2026: Immunotherapy, HIF-2 Alpha, TKIs      ← correct
<h1>     Reaching Out-of-School Maasai Girls With HPV Vaccination...  ← wrong
```

This is a CMS/front-end bug. After every index build, `data/manifest.json`
holds the full list under `h1_mismatches`. That list is the bug report for the
dev team. The tool works around it by falling back to `<title>` when the H1 is
inconsistent.

## Post-processing: sentence rewriting with Claude

The `skills/internal-link-rewriter/` directory contains a Claude skill that
post-processes the Excel output. Upload the downloaded workbook to Claude and
ask it to "rewrite the insertion rows" or "make these writer-ready."

The skill:
1. Reads every "Needs insertion" row
2. Rewrites the sentence with the anchor phrase woven in naturally
3. Validates: anchor verbatim, no new medical claims, length check
4. Returns the updated Excel with rewritten sentences in blue text

This produces much higher quality rewrites than any free LLM API because Claude
reads the full context and understands medical terminology.

## Quick start

```bash
pip install -r requirements.txt
python tests/test_rules.py        # 116 tests, all must pass
streamlit run app.py
python -m indexer.build_index     # rebuild the full index
```

## Retrieval pipeline

```
Source article
  │
  ├── Embedding retrieval (FAISS + BM25, fused with RRF)
  ├── Keyword scan: grep every page's body text for the article's disease terms
  ├── Title similarity: same-topic articles always surface
  ├── Awareness and conference matching on disease terms
  └── IJCCD gate: journal articles only when the disease matches
  │
  Merge → anchor selection → SOP rules → scoring → output
```

## Layout

```
app.py                                Streamlit front end
config/                               settings, URL rules, selectors, synonym map
indexer/                              sitemap, crawler, extractor (with H1 guard), blocks, chunker, embedder, build
engine/keyword_scan.py                disease terms, body-text grep, awareness matching
engine/cannibalization_data.py        Semrush export to real query-competition map
engine/llm_rewrite.py                 optional: sentence rewriting via Groq/HF free tier
output/excel_writer.py                formatted workbook
skills/internal-link-rewriter/        Claude skill for post-processing the Excel
tests/test_rules.py                   116 unit tests
```

Every threshold lives in `config/settings.py`. Nothing else hard-codes one.
