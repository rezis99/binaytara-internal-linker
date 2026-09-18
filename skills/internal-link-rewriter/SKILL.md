---
name: internal-link-rewriter
description: Post-process a Binaytara Internal Link Recommender Excel file. Reads each "Needs insertion" row, rewrites the sentence with the anchor phrase woven in naturally, validates the result, and returns the updated Excel. Use when the user uploads an .xlsx file from the internal link recommender tool and asks to rewrite, polish, or finalize the sentences. Also triggers on "make these writer-ready" or "rewrite the insertion rows."
---

# Internal Link Rewriter Skill

## What this does

The Binaytara Internal Link Recommender produces an Excel workbook with two
types of suggestions:

1. **"Exact in text" / "Synonym in text"**: the anchor phrase already appears in
   the article. The "Modified Sentence" column shows the markdown link in place.
   These rows are already writer-ready and this skill does not touch them.

2. **"Needs insertion"**: the anchor phrase does NOT appear in the sentence. The
   "Modified Sentence" column currently contains a manual instruction like
   'Writer to incorporate the phrase "stomach cancer" naturally...'
   Writers said this is unusable; they need a complete proposed sentence.

This skill rewrites every "Needs insertion" row into a complete sentence that
contains the anchor phrase naturally, with the link already in place. The writer
can then approve, tweak, or reject each one.

## When to use

- User uploads a `.xlsx` file from the internal link recommender
- User says "rewrite the insertion rows", "make these writer-ready", "finalize
  the link suggestions", "polish the Excel"
- The file has sheets starting with "Give" or "Recv" and a column named
  "Match Type" containing "Needs insertion"

## Step-by-step process

### 1. Read the uploaded Excel

```python
from openpyxl import load_workbook
import copy

wb = load_workbook("/mnt/user-data/uploads/<filename>.xlsx")
```

### 2. Find all "Needs insertion" rows

For each sheet whose name starts with "Give", "Other", or "Recv":
- Find the header row (row 1 or row 2, depending on whether a target URL line
  is present at row 1 on Recv sheets).
- Locate these columns by header name:
  - "Match Type"
  - "Existing Sentence"
  - "Modified Sentence"
  - "Anchor Text"
  - "Target Page Title" (Give sheets) or "Source Page Title" (Recv sheets)
  - "Target Page Link" (Give sheets) or "Source Page" (Recv sheets)

- Collect every row where Match Type == "Needs insertion"

### 3. Rewrite each sentence

For each collected row, compose this prompt and send it to Claude's own
reasoning (you ARE Claude, so just think through it directly):

**Context you have:**
- `existing_sentence`: the original sentence from the article
- `anchor`: the phrase that must appear verbatim in the rewrite
- `target_title`: what the linked page is about
- `target_url`: where the link points

**Rewriting rules (follow strictly):**
1. The anchor phrase MUST appear VERBATIM in the rewritten sentence, exactly as
   given (same spelling, same case pattern).
2. Do NOT add any medical claims, statistics, drug names, or facts that are not
   in or clearly implied by the original sentence.
3. Keep the original meaning and tone.
4. Keep similar length: the rewrite should be between 0.7x and 1.5x the word
   count of the original.
5. The rewrite should read as natural English prose, not as an SEO insertion.
6. Wrap the anchor phrase as a markdown link: `[anchor](target_url)`
7. If the sentence is a direct quote (starts and ends with quotation marks),
   do NOT rewrite it. Return it unchanged with a note that quotes cannot be
   modified.

**Output:** the rewritten sentence with the markdown link in place.

### 4. Validate each rewrite

Before writing back to the Excel, validate:
- [ ] The anchor phrase appears verbatim in the rewrite (case-insensitive
      word-boundary match)
- [ ] The markdown link `[anchor](url)` is correctly formed
- [ ] Word count is between 0.7x and 1.5x of the original
- [ ] No markdown artifacts like `[`, `]`, or extra `(` outside the link
- [ ] The rewrite is not identical to the original (something changed)

If validation fails, try once more with a stricter prompt. If it fails again,
leave the original "Writer to incorporate..." instruction and append
"[auto-rewrite failed; manual edit needed]".

### 5. Write back to the Excel

For each validated rewrite:
- Replace the "Modified Sentence" cell value with the rewrite
- Change the cell's font color to dark blue (#1F3864) so the writer can
  visually distinguish auto-rewritten sentences from exact-match ones

### 6. Save and present

```python
wb.save("/mnt/user-data/outputs/binaytara-link-suggestions-rewritten.xlsx")
```

Present the file with a summary:
- How many "Needs insertion" rows were found
- How many were successfully rewritten
- How many failed validation (if any)
- Remind the writer to review each blue-text sentence before implementation

## Example rewrite

**Input:**
- Existing: "Kidney cancer affects hundreds of thousands of people every year."
- Anchor: "kidney cancer awareness"
- Target: "Kidney Cancer Awareness Month"
- URL: https://binaytara.org/cancernews/article/kidney-cancer-awareness-month

**Output:**
"During [kidney cancer awareness](https://binaytara.org/cancernews/article/kidney-cancer-awareness-month) month and beyond, it is worth noting that this disease affects hundreds of thousands of people every year."

**Why this works:**
- "kidney cancer awareness" appears verbatim ✓
- No new medical claims added ✓
- Original meaning preserved (the statistic is unchanged) ✓
- Reads naturally; a human would write it this way ✓

## Example: quote protection

**Input:**
- Existing: '"Early detection is a major gap in the field," said Dr. Zakharia.'
- Anchor: "early detection"

**Output:** Leave unchanged. Append note: "Direct quote; cannot be rewritten."

## What NOT to do

- Do NOT rewrite "Exact in text" or "Synonym in text" rows. They already have
  the link in place.
- Do NOT change the anchor phrase. "stomach cancer" must stay "stomach cancer",
  not "gastric cancer" or "cancers of the stomach."
- Do NOT add disclaimers, caveats, or hedging language ("may", "potentially")
  that the original does not contain.
- Do NOT restructure the sentence so heavily that the paragraph flow is broken.
  The sentence must still make sense in context.
- Do NOT touch rows on the "Summary" sheet or any metadata.

## Handling IJCCD (journal) rows

If the Notes column contains "IJCCD research paper: consider placing in
references", note this in the summary output. The rewrite should frame the
link as a citation-style reference rather than a body-prose insertion:

"...as demonstrated in recent research on [racial disparities in stomach
cancer](url) outcomes."

## Error handling

- If the file has no "Needs insertion" rows: say so and return the file unchanged.
- If a sheet has unexpected column names: skip that sheet and note it.
- If the file is not from the internal link recommender (no Match Type column):
  tell the user this skill is designed for the recommender's output format.
