# Binaytara Internal Link Recommender (v5)

The tool scans 1,680 pages in seconds and retrieves candidates. Claude judges
which ones actually matter and rewrites the sentences. Each does what it is
best at.

**Start here: [SETUP.md](SETUP.md)** for install and operation guide.

## How v5 works (tool + Claude skill)

```
1. TOOL (automated, ~8 seconds per article)
   Sitemap → crawl → embed → retrieve candidates → SOP rules → Excel output
   The tool is fast at scanning the full site but weak at judging whether
   "kidney cancer immunotherapy" is relevant to "breast cancer immunotherapy"
   (they are not; they share vocabulary, not topic).

2. CLAUDE SKILL (one upload, ~2 minutes)
   Upload the Excel → Claude filters irrelevant suggestions → rewrites
   "Needs insertion" sentences → returns a clean, writer-ready file.
   Claude reads content the way a human reviewer does and understands that
   different cancer types are different topics despite sharing vocabulary.

3. WRITER receives a file where:
   YES rows in green = approved, ready to implement
   Blue text = sentence was auto-rewritten with the link in place
   Gray strikethrough = filtered out, do not implement
   Red text = needs manual attention (direct quotes, failed rewrites)
```

## What the section filters mean

| Filter | What it covers | Example URLs |
|---|---|---|
| TCN | The Cancer News articles | `/cancernews/article/stomach-cancer-101` |
| IJCCD | Journal research papers (Give only; excluded from Receive) | `/journal/article/129490-...` |
| Blog | Organizational news | `/news/education/...` |
| Conference | Individual conference pages | `/projects/conferences/2026-seattle-lung-...` |
| Project | Project hubs (OncoBlast, conference index) | `/projects/oncoblast` |
| Static | Evergreen pages (about, grants, contact) | `/about`, `/research-grants` |
| Contributor | Author profile pages | `/cancernews/contributors/dr-jane-smith` |

## What changed in v5

| Change | What it does |
|---|---|
| Bidirectional receive-side keyword scan | v4 only searched OTHER pages for the source's terms. v5 also searches the SOURCE for other pages' terms. This catches "Nepal's Cancer Burden has 'alcohol consumption' in its text → should link to the alcohol article" |
| Review Context column in Excel | Each row now explains WHY the tool suggested this link (source topic, target topic, match basis). Claude reads this column to make faster filtering decisions |
| Expanded Claude skill | One skill does both filtering (remove breast cancer from kidney cancer suggestions) and rewriting ("Needs insertion" → complete sentences). Replaces the separate rewriter skill from v4 |
| IJCCD fully excluded from Receive | Journal papers cannot be edited to add links |
| IJCCD give-side disease gate | Journal articles only suggested when their disease matches the source |

## Maintaining the Semrush cannibalization data

1. Export from Semrush: Organic Research → binaytara.org → Positions → Export XLSX
2. Run: `python -m engine.cannibalization_data path/to/export.xlsx`
3. Upload the new `data/cannibalization.json` to GitHub

The tool works without this file (falls back to title heuristic). Update it
monthly or after publishing a batch of new articles.

## The CMS H1 defect

~11% of TCN articles serve a wrong `<h1>`. The tool detects this and falls back
to `<title>`. After each build, `data/manifest.json` lists every affected URL
under `h1_mismatches`. Share that list with the dev team.

## Quick start

```bash
pip install -r requirements.txt
python tests/test_rules.py
streamlit run app.py
python -m indexer.build_index
```

## Layout

```
app.py                                       Streamlit front end
config/                                      settings, URL rules, selectors, synonym map
indexer/                                     sitemap, crawler, extractor (H1 guard), build
engine/keyword_scan.py                       disease terms, body-text grep (bidirectional), awareness
engine/cannibalization_data.py               Semrush export to query-competition map
engine/llm_rewrite.py                        optional Groq/HF sentence rewriting
output/excel_writer.py                       formatted workbook with Review Context column
skills/internal-link-filter-and-rewrite/     Claude skill: filter + rewrite in one pass
tests/test_rules.py                          116 unit tests
```
