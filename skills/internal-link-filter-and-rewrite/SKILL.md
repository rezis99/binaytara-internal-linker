---
name: internal-link-filter-and-rewrite
description: >
  Post-process a Binaytara Internal Link Recommender Excel file. Does two jobs
  in one pass: (1) filters out irrelevant suggestions that the embedding model
  included because of shared oncology vocabulary, and (2) rewrites every "Needs
  insertion" sentence with the anchor phrase woven in naturally. Use when the
  user uploads an .xlsx from the internal link recommender tool. Triggers on
  "filter and rewrite", "clean up this file", "make these writer-ready",
  "review the suggestions", or "finalize the link suggestions."
---

# Internal Link Filter and Rewriter

## Why this skill exists

The Binaytara Internal Link Recommender tool scans 1,680 pages in seconds using
embeddings and keyword matching. It is fast at retrieval but weak at judgment:
on an oncology site where every page shares words like "cancer," "treatment,"
"immunotherapy," and "patients," the embedding model cannot distinguish between
cancer types. A kidney cancer article gets suggestions to link to breast cancer,
lung cancer, and lymphoma articles because they all use similar clinical
vocabulary.

This skill adds the judgment layer. You (Claude) read each suggestion, decide
whether it is genuinely relevant from a reader's perspective, remove the ones
that are not, and rewrite the "Needs insertion" sentences into complete
writer-ready prose.

The tool handles retrieval (what it is good at). You handle judgment (what you
are good at). The writer gets a clean file they can implement directly.


## When this skill triggers

Any of these:
  The user uploads a .xlsx file AND mentions "filter", "rewrite", "clean up",
  "finalize", "review", "writer-ready", or "link suggestions."
  The user says "run the internal link filter" or similar.
  The file has sheets starting with "Give", "Other", or "Recv" with columns
  including "Match Type", "Review Context", and "Modified Sentence."


## Step-by-step execution

### Step 1: Read the Excel and understand the structure

```python
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill
import re

filepath = "/mnt/user-data/uploads/<FILENAME>.xlsx"
wb = load_workbook(filepath)
```

The workbook has these sheet types:
  "Summary": overview stats. Do not modify.
  "Give - ...": outbound link suggestions (links FROM this article TO targets).
  "Other - ...": lower-relevance outbound suggestions.
  "Recv - ...": inbound link suggestions (other pages that should link TO this article).

Column layout (v5):
  Use? | Relevance | Match Type | Keyword Competition | Anchor Text |
  Target Page Title (or Source Page Title) | Target Page Link (or Source Page) |
  Section | Existing Sentence | Modified Sentence | Review Context | Notes


### Step 2: Read every data row into a working list

```python
SHEET_PREFIXES = ("Give", "Other", "Recv")

def find_header_row(ws):
    for row_num in (1, 2, 3):
        vals = [str(c.value or "").strip() for c in ws[row_num]]
        if "Match Type" in vals:
            return row_num
    return None

def col_index(headers, name):
    for i, h in enumerate(headers, 1):
        if str(h).strip().lower() == name.lower():
            return i
    return None

all_rows = []  # working list for all sheets

for ws in wb.worksheets:
    if not any(ws.title.startswith(p) for p in SHEET_PREFIXES):
        continue
    hdr_row = find_header_row(ws)
    if hdr_row is None:
        continue
    headers = [str(c.value or "") for c in ws[hdr_row]]

    ci = {}
    for name in ["Use?", "Relevance", "Match Type", "Keyword Competition",
                  "Anchor Text", "Section", "Existing Sentence",
                  "Modified Sentence", "Review Context", "Notes"]:
        ci[name] = col_index(headers, name)

    # Title and link columns vary by sheet type
    if ws.title.startswith("Recv"):
        ci["title"] = col_index(headers, "Source Page Title")
        ci["link"] = col_index(headers, "Source Page")
    else:
        ci["title"] = col_index(headers, "Target Page Title")
        ci["link"] = col_index(headers, "Target Page Link")

    for row_num in range(hdr_row + 1, ws.max_row + 1):
        mt = str(ws.cell(row_num, ci["Match Type"]).value or "").strip()
        if not mt:
            continue

        link_cell = ws.cell(row_num, ci["link"]) if ci["link"] else None
        url = ""
        if link_cell and link_cell.hyperlink:
            url = link_cell.hyperlink.target
        elif link_cell:
            url = str(link_cell.value or "").strip()

        all_rows.append({
            "ws": ws,
            "sheet": ws.title,
            "row": row_num,
            "match_type": mt,
            "anchor": str(ws.cell(row_num, ci["Anchor Text"]).value or "").strip(),
            "title": str(ws.cell(row_num, ci["title"]).value or "").strip() if ci["title"] else "",
            "url": url,
            "existing": str(ws.cell(row_num, ci["Existing Sentence"]).value or "").strip(),
            "modified": str(ws.cell(row_num, ci["Modified Sentence"]).value or "").strip(),
            "context": str(ws.cell(row_num, ci["Review Context"]).value or "").strip() if ci["Review Context"] else "",
            "notes": str(ws.cell(row_num, ci["Notes"]).value or "").strip() if ci["Notes"] else "",
            "relevance": str(ws.cell(row_num, ci["Relevance"]).value or "").strip(),
            "ci": ci,  # column indices for writing back
        })
```


### Step 3: FILTER — judge each suggestion for genuine topical relevance

This is the most important step. For each row, decide: **would a reader of the
source article benefit from this link?**

Read the "Review Context" column. It tells you:
  Source: [what the article is about]
  Target: [what the linked page is about]
  Match basis: [why the tool suggested this: embedding similarity, keyword
  overlap, title similarity, or anchor phrase found in text]

**Filtering rules (apply in this order):**

RULE F1: DIFFERENT DISEASE = REJECT.
If the source article is about kidney cancer and the target is about breast
cancer, lung cancer, lymphoma, myeloma, or any other unrelated cancer type,
mark it REJECT. The fact that both pages mention "immunotherapy" or "treatment
advances" does not make them relevant to each other.

Exception: if the source article explicitly compares multiple cancer types
(e.g., "Immunotherapy Across Solid Tumors"), then cross-cancer links are valid.

RULE F2: ORGANIZATIONAL PAGE ≠ TOPICAL PAGE.
Reject links to volunteer programs, partnership pages, donation pages, community
health assessments, or other pages that are organizationally related to
Binaytara but not topically related to the source article's disease.

RULE F3: CONFERENCE PAGE MUST MATCH THE DISEASE.
A conference summary about breast cancer is not relevant to a kidney cancer
article just because both mention "ASCO." Only keep conference links where the
conference specifically covers the source article's disease.

RULE F4: GENERIC ANCHOR = REJECT.
If the anchor text is a generic phrase that could link to any page ("all cancer",
"cancer awareness", "risk factors", "early detection", "clinical trials",
"treatment options", "new study"), reject it. These are not useful links.

RULE F5: JOURNAL ARTICLE MUST MATCH THE STUDY.
If the target is an IJCCD journal article, it is only relevant if the study's
disease or topic directly matches the source article. A SEER analysis of stomach
cancer is relevant to a stomach cancer article. A study of health needs in
Janakpurdham is not relevant to an alcohol-cancer article.

RULE F6: KEEP AWARENESS AND OVERVIEW PAGES.
If the target is a "[Disease] Awareness Month" or "[Disease] 101" page and the
source article is about the same disease, always keep it. These are high-value
reader-journey links.

RULE F7: KEEP RISK-FACTOR CROSS-LINKS.
If the source article discusses a risk factor (alcohol, smoking, obesity) and
the target is about a cancer type caused by that risk factor, keep it. The
alcohol article should link to esophageal cancer, stomach cancer, breast cancer,
etc. because alcohol is a confirmed risk factor for each.

RULE F8: WHEN IN DOUBT, REJECT.
If you are not confident the link is genuinely useful to a reader, reject it.
It is better to miss a marginal link than to suggest one that wastes the
writer's time or confuses the reader.


### Step 4: Mark filtered rows in the Excel

```python
REJECT_FILL = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")  # light yellow
REJECT_FONT = Font(color="808080", strikethrough=True)  # gray strikethrough

filtered_count = 0

for item in all_rows:
    # Your filtering decision: True = keep, False = reject
    # keep = <your judgment based on F1-F8>
    # reject_reason = <short explanation>

    if not keep:
        ws = item["ws"]
        row = item["row"]
        ci = item["ci"]
        # Mark the "Use?" column with "NO" and the reason
        if ci["Use?"]:
            ws.cell(row, ci["Use?"]).value = "NO"
            ws.cell(row, ci["Use?"]).font = Font(color="C00000", bold=True)
        # Add rejection reason to Notes
        if ci["Notes"]:
            old_notes = str(ws.cell(row, ci["Notes"]).value or "")
            ws.cell(row, ci["Notes"]).value = f"[FILTERED: {reject_reason}] {old_notes}"
        # Gray out the row
        for col in range(1, len([c for c in ws[1]]) + 1):
            ws.cell(row, col).font = REJECT_FONT
        filtered_count += 1
    else:
        # Mark as approved
        if ci["Use?"]:
            ws.cell(row, ci["Use?"]).value = "YES"
            ws.cell(row, ci["Use?"]).font = Font(color="006100", bold=True)
```


### Step 5: REWRITE — fix every "Needs insertion" row that survived filtering

For rows that passed the filter AND have Match Type == "Needs insertion",
rewrite the sentence following these rules:

RULE R1: ANCHOR VERBATIM.
The anchor phrase must appear VERBATIM in the rewritten sentence. "stomach
cancer" stays "stomach cancer", never "gastric cancer" or "cancers of the
stomach."

RULE R2: NO NEW MEDICAL CLAIMS.
Do NOT add statistics, drug names, treatment names, survival rates, or any
medical fact not in the original sentence. This is a cancer education website.
Incorrect medical information is harmful.

RULE R3: PRESERVE MEANING.
The original sentence's factual content must be fully preserved. Do not drop
clauses, change numbers, or alter the conclusion.

RULE R4: NATURAL PHRASING.
The rewrite should read as if a human wrote it. Avoid "for more information
about", "learn more about", "as discussed in our article about." Prefer weaving
the anchor into an existing clause or adding a brief natural subordinate clause.

RULE R5: LENGTH CONTROL.
The rewrite should be between 0.7x and 1.5x the word count of the original.

RULE R6: LINK FORMATTING.
Wrap the anchor phrase as a markdown link: [anchor](url)
The link must appear exactly once.

RULE R7: QUOTE PROTECTION.
If the sentence starts and ends with quotation marks, it is a direct quote.
Do NOT rewrite it. Mark it: "[Direct quote: cannot be modified. Place this link
on an adjacent non-quote sentence.]"

RULE R8: JOURNAL CITATION FRAMING.
If the Notes column mentions "IJCCD research paper," frame the link as a
research citation: "as reported in a [study of X](url)" rather than body prose.

### Validation function

```python
def validate_rewrite(original, rewrite, anchor, url):
    if not rewrite or not rewrite.strip():
        return False, "Empty"
    pattern = r"\b" + re.escape(anchor) + r"\b"
    if not re.search(pattern, rewrite, re.IGNORECASE):
        return False, f"Anchor '{anchor}' missing"
    if f"]({url})" not in rewrite:
        return False, "Link malformed"
    orig_words = len(original.split())
    new_words = len(rewrite.split())
    if orig_words > 0:
        ratio = new_words / orig_words
        if ratio < 0.7 or ratio > 1.5:
            return False, f"Length ratio {ratio:.1f}x"
    cleaned = rewrite.replace(f"[{anchor}]({url})", "PLACEHOLDER", 1)
    if "[" in cleaned or "](" in cleaned:
        return False, "Stray markdown"
    for bp in ["here is", "note:", "rewritten:", "i've", "the rewrite"]:
        if bp in rewrite.lower():
            return False, f"Meta-commentary: '{bp}'"
    return True, "OK"
```

### Write back

```python
BLUE_FONT = Font(color="1F3864")  # dark blue = auto-rewritten
RED_FONT = Font(color="C00000")   # red = failed, needs manual edit

rewritten_count = 0
failed_count = 0
quote_count = 0

for item in all_rows:
    if item was rejected in Step 4:
        continue
    if item["match_type"] != "Needs insertion":
        continue

    # Quote protection
    stripped = item["existing"].strip()
    if (stripped.startswith('"') and stripped.endswith('"')) or \
       (stripped.startswith('\u201c') and stripped.endswith('\u201d')):
        msg = "[Direct quote: cannot be modified. Place link on adjacent sentence.]"
        item["ws"].cell(item["row"], item["ci"]["Modified Sentence"]).value = msg
        item["ws"].cell(item["row"], item["ci"]["Modified Sentence"]).font = RED_FONT
        quote_count += 1
        continue

    # Rewrite the sentence (you do this with your own reasoning, not an API)
    # rewrite = <your rewritten sentence>
    # is_valid, reason = validate_rewrite(item["existing"], rewrite, item["anchor"], item["url"])

    # If valid:
    #   item["ws"].cell(item["row"], item["ci"]["Modified Sentence"]).value = rewrite
    #   item["ws"].cell(item["row"], item["ci"]["Modified Sentence"]).font = BLUE_FONT
    #   rewritten_count += 1

    # If failed after retry:
    #   item["ws"].cell(item["row"], item["ci"]["Modified Sentence"]).value += " [auto-rewrite failed]"
    #   item["ws"].cell(item["row"], item["ci"]["Modified Sentence"]).font = RED_FONT
    #   failed_count += 1
```


### Step 6: Save and present

```python
output = "/mnt/user-data/outputs/binaytara-links-final.xlsx"
wb.save(output)
```

Present the file with this summary:

```
FILTER + REWRITE SUMMARY

  Total suggestions in file:           83
  Filtered out (irrelevant):           48  (58%)
  Kept (genuinely relevant):           35  (42%)

  Of the kept suggestions:
    Already writer-ready (Exact/Synonym):  22
    "Needs insertion" rewritten:           11
    Direct quotes (skipped):                1
    Rewrite failed (manual needed):         1

FILTER DECISIONS (for your review):

| Sheet       | Row | Target                      | Decision | Reason                           |
|-------------|-----|-----------------------------|----------|----------------------------------|
| Give - kidn | 3   | Small Cell Lung Cancer 2026 | REJECT   | Different disease (lung ≠ kidney) |
| Give - kidn | 5   | Kidney Cancer Awareness     | KEEP     | Same disease, awareness page      |
| Give - kidn | 8   | Breast Cancer CDK4/6        | REJECT   | Different disease (breast ≠ kidney)|
| Recv - alco | 2   | Cancer Prevention Insights  | REJECT   | Already links to this article     |
| ...         |     |                             |          |                                   |

REWRITE DECISIONS (blue text = auto-rewritten):

| Sheet       | Row | Anchor             | Status    | Rewrite preview (first 80 chars)    |
|-------------|-----|--------------------|-----------|-------------------------------------|
| Give - kidn | 5   | kidney awareness   | REWRITTEN | ...efforts for [kidney awareness]...|
| Recv - alco | 4   | alcohol use        | QUOTE     | [Direct quote: cannot be modified]  |

Every blue-text sentence was auto-rewritten. Gray strikethrough rows were
filtered out. Red-text rows need manual attention. Review before forwarding
to the writer.
```


## Rewriting examples

### Example 1: Disease term insertion
Existing: "Alcohol is also linked to cancers of the digestive system, including
cancers that form in the stomach lining."
Anchor: "stomach cancer"
URL: https://binaytara.org/cancernews/article/stomach-cancer-101

Rewrite: "Alcohol is also linked to cancers of the digestive system, including
[stomach cancer](https://binaytara.org/cancernews/article/stomach-cancer-101)
that forms in the stomach lining."

### Example 2: Awareness page link
Existing: "Kidney cancer affects hundreds of thousands of people every year."
Anchor: "kidney cancer awareness"
URL: https://binaytara.org/cancernews/article/kidney-cancer-awareness-month

Rewrite: "Kidney cancer affects hundreds of thousands of people every year,
underscoring the importance of [kidney cancer awareness](https://binaytara.org/cancernews/article/kidney-cancer-awareness-month)
efforts worldwide."

### Example 3: Journal citation
Existing: "Racial disparities in cancer outcomes remain a significant concern."
Anchor: "racial disparities in stomach cancer"
URL: https://binaytara.org/journal/article/129490-racial-disparities-...
Notes: "IJCCD research paper"

Rewrite: "Racial disparities in cancer outcomes remain a significant concern,
as highlighted by research on [racial disparities in stomach cancer](https://binaytara.org/journal/article/129490-racial-disparities-...)
among adolescent and young adult patients."


## Filtering examples

### REJECT: Different disease
Review Context: "Source: Kidney Cancer Immunotherapy. Target: Small Cell Lung
Cancer in 2026. Match basis: embedding similarity 0.65"
Decision: REJECT. Reason: Different disease (lung ≠ kidney). The embedding
matched because both discuss immunotherapy, but the reader of a kidney cancer
article does not need a link to a lung cancer article.

### KEEP: Same disease, different angle
Review Context: "Source: Kidney Cancer Immunotherapy. Target: Kidney Cancer
Adjuvant IO. Match basis: keyword overlap 45%, embedding 0.72"
Decision: KEEP. Same disease (kidney cancer), adjacent topic (adjuvant IO vs
first-line IO). A reader interested in kidney cancer treatment would want this.

### REJECT: Organizationally related, not topically related
Review Context: "Source: Alcohol and Cancer Risk. Target: Community Health Needs
Assessment: Janakpurdham. Match basis: embedding similarity 0.58"
Decision: REJECT. The CHNA is an organizational report about a specific city's
health needs. It is not about alcohol or cancer risk.

### KEEP: Risk factor cross-link
Review Context: "Source: Alcohol and Cancer Risk. Target: Esophageal Cancer
Awareness. Match basis: keyword overlap 30%, anchor found in text"
Decision: KEEP. Alcohol is a confirmed risk factor for esophageal cancer. The
alcohol article discusses esophageal cancer specifically. This is a high-value
reader-journey link.

### REJECT: Generic anchor to category hub
Anchor: "all cancer"
Target: All Articles page
Decision: REJECT. "All cancer" in the article means "all types of cancer," not
the article index page. This anchor is misleading.


## What NOT to do

Do NOT approve a suggestion just because the tool rated it "High relevance."
The tool's relevance rating is based on embedding similarity, which is the exact
signal that fails on oncology content. Trust your own judgment over the tool's
score.

Do NOT reject a suggestion just because it is "Needs insertion." The anchor
might not appear in the text, but the link could still be highly relevant. Check
the target page's topic, not just the match type.

Do NOT change the anchor phrase. If the anchor is "stomach cancer," it stays
"stomach cancer." You can change which sentence the anchor is placed in, but
not the anchor itself.

Do NOT add medical claims during rewriting. If the original sentence says
"Kidney cancer affects hundreds of thousands," do not add "with a 5-year
survival rate of 76%" unless that number is already in the original.

Do NOT touch the Summary sheet or any styling outside the data rows.
