"""Unit tests for the SOP rule engine and the block classifier.

Every test here corresponds to a rule that, if broken, produces a suggestion a
writer must not act on.
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import url_rules as ur                              # noqa: E402
from engine import anchor, cannibalization, conference, rules    # noqa: E402
from indexer.blocks import Block, classify                       # noqa: E402
from indexer.extractor import person_name_variants, strip_brand  # noqa: E402
from output import excel_writer                                  # noqa: E402

FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {detail}")
        FAILURES.append(name)


def blocks(*specs):
    return [Block(index=i, kind=k, text=t) for i, (k, t) in enumerate(specs)]


LONG = ("This paragraph is deliberately long enough to clear the minimum word "
        "count so that it is eligible for a suggestion under the rules we test. "
        "It discusses kidney cancer treatment in general terms for the purpose.")


def test_blocks():
    print("\nBlock classifier")
    b = classify(blocks(("p", LONG), ("p", LONG), ("p", LONG)))
    check("R1 first paragraph skipped", b[0].skip_reason == "FIRST_PARAGRAPH")
    check("R1 later paragraphs eligible", b[1].eligible and b[2].eligible)

    b = classify(blocks(("p", LONG), ("h2", "Key Takeaways"), ("p", LONG),
                        ("h2", "Background"), ("p", LONG)))
    check("R2 Key Takeaways region skipped", b[2].skip_reason == "KEY_TAKEAWAYS")
    check("R2 region ends at next heading", b[4].eligible)

    b = classify(blocks(("p", LONG), ("p", LONG), ("h2", "Works discussed"),
                        ("p", LONG), ("p", LONG)))
    check("R3 references region skipped",
          all(x.skip_reason == "REFERENCES" for x in b[2:]))

    quote = ('Early detection is a major gap in the field, he said, and many '
             'patients are diagnosed at a late stage with metastatic disease '
             'because they do not experience symptoms early on in the course.')
    b = classify(blocks(("p", LONG), ("p", f'"{quote}"')))
    check("R4 direct quote skipped", b[1].skip_reason == "QUOTE")

    apostrophe = ("Kidney cancer's rising incidence means awareness matters, and "
                  "the theme encourages people to support patients and caregivers "
                  "through the year with sustained and practical commitment here.")
    b = classify(blocks(("p", LONG), ("p", apostrophe)))
    check("apostrophe is not a quote mark", b[1].eligible,
          f"got {b[1].skip_reason}")

    b = classify(blocks(("p", LONG), ("p", "Too short to matter at all.")))
    check("short block skipped", b[1].skip_reason == "SHORT")

    b = classify(blocks(("p", LONG),
                        ("p", "The content provided is for informational purposes "
                              "only and is not intended as medical advice ever.")))
    check("disclaimer skipped", b[1].skip_reason == "DISCLAIMER")

    faq = ("What are the symptoms of kidney cancer? Common symptoms include blood "
           "in the urine, a lump or pain in the side or lower back, and weight "
           "loss that the patient did not intend to experience at all.")
    b = classify(blocks(("p", LONG), ("h2", "FAQ"), ("p", faq)))
    check("FAQ answers remain eligible", b[2].eligible, f"got {b[2].skip_reason}")


def test_urls():
    print("\nURL rules (R9, subdomains)")
    check("main domain allowed",
          ur.is_allowed("https://binaytara.org/cancernews/article/x"))
    for bad in ["https://conference.binaytara.org/x",
                "https://education.binaytara.org/x",
                "https://abstracts.binaytara.org/x",
                "https://give.binayfoundation.org/x",
                "https://education.binayfoundation.org/x"]:
        check(f"excluded {bad.split('//')[1].split('/')[0]}", not ur.is_allowed(bad))
    check("contributor index page excluded",
          not ur.is_allowed("https://binaytara.org/cancernews/contributors"))
    check("contributor detail page allowed",
          ur.is_allowed("https://binaytara.org/cancernews/contributors/jane-doe"))
    check("pdf excluded", not ur.is_allowed("https://binaytara.org/a/report.pdf"))
    check("query string rejected by R9",
          not rules.url_ok("https://binaytara.org/x?utm_source=a"))
    check("relative URL rejected by R9", not rules.url_ok("/cancernews/article/x"))
    check("section mapping",
          ur.section_of("https://binaytara.org/journal/article/1-x") == "IJCCD")


def test_r7():
    print("\nR7 body links versus navigation links")
    page = {"body_internal_links": ["https://binaytara.org/a"],
            "nav_internal_links": ["https://binaytara.org/b"]}
    check("body link blocks a suggestion",
          rules.already_linked_in_body(page, "https://binaytara.org/a"))
    check("nav link does NOT block a suggestion",
          not rules.already_linked_in_body(page, "https://binaytara.org/b"))


def test_anchor():
    print("\nR8 anchor selection")
    target = {"url": "https://binaytara.org/cancernews/article/immunotherapy",
              "h1": "Immunotherapy Advances in Kidney Cancer",
              "title_clean": "Immunotherapy Advances", "meta_description": ""}
    got = anchor.select(target, "New immunotherapy advances are changing care.", {})
    check("exact match found", got["match_type"] == "Exact in text", got)
    check("anchor within 2 to 5 words", rules.anchor_word_count_ok(got["anchor"]),
          got["anchor"])

    got = anchor.select(target, "Patients now receive very different regimens.", {})
    check("falls back to needs insertion", got["match_type"] == "Needs insertion")

    syn = {"url": "https://binaytara.org/x", "h1": "Renal Cell Carcinoma Treatment",
           "title_clean": "", "meta_description": ""}
    got = anchor.select(syn, "Options for kidney cancer treatment have expanded "
                             "considerably over the past decade of research.", {})
    check("synonym map bridges RCC to kidney cancer",
          got["match_type"] in ("Synonym in text", "Partial in text"), got["match_type"])

    guide = {"https://binaytara.org/x": {"approved_anchors": ["renal cell carcinoma treatment"]}}
    got = anchor.select(syn, "We reviewed renal cell carcinoma treatment options today.", guide)
    check("approved guide anchor wins", got["tier"] == 1, got)

    serp = {"url": "https://binaytara.org/y", "h1": "",
            "title_clean": "", "meta_description": "Learn the warning signs of cancer"}
    cands = [a for a, _t in anchor.candidates(serp, {})]
    check("SERP verbs filtered from anchors",
          not any(c.lower().startswith("learn") for c in cands), cands[:3])


def test_caps():
    print("\nR5 and R6 caps")
    rows = [
        {"block_index": 1, "target_url": "u1", "score": 0.9},
        {"block_index": 1, "target_url": "u2", "score": 0.8},
        {"block_index": 1, "target_url": "u3", "score": 0.7},
        {"block_index": 2, "target_url": "u1", "score": 0.95},
    ]
    out = rules.enforce_caps(rows)
    check("R6 each target only once", len({r["target_url"] for r in out}) == len(out))
    check("R6 keeps the best placement for u1",
          [r for r in out if r["target_url"] == "u1"][0]["block_index"] == 2)
    per = {}
    for r in out:
        per[r["block_index"]] = per.get(r["block_index"], 0) + 1
    check("R5 max two per paragraph", all(v <= 2 for v in per.values()), per)


def test_contributor():
    print("\nR10 contributor gating")
    page = {"url": "https://binaytara.org/cancernews/contributors/jane-smith",
            "person_names": person_name_variants("Jane Smith, MD")}
    ok, _ = rules.contributor_named(page, "Jane Smith led the study this year.")
    check("full name matches", ok)
    ok, _ = rules.contributor_named(page, "Dr. Smith led the study this year.")
    check("surname with title matches", ok)
    ok, _ = rules.contributor_named(page, "The smith worked on the trial design.")
    check("bare surname does NOT match", not ok)
    ok, _ = rules.contributor_named(page, "A study of kidney cancer was published.")
    check("unrelated text does not match", not ok)
    other = {"url": "https://binaytara.org/cancernews/article/x", "person_names": []}
    ok, _ = rules.contributor_named(other, "anything")
    check("non-contributor pages unaffected", ok)


def test_conference():
    print("\nR11 conference expiry")
    today = date(2026, 9, 16)
    past = {"section": "Conference", "url": "https://binaytara.org/projects/conferences/a",
            "event": {"startDate": "2025-10-02", "endDate": "2025-10-03"}, "h1": "", "title": ""}
    keep, _n, _f = rules.conference_ok(past, 0.95, today)
    check("expired event dropped", not keep)
    future = dict(past, event={"startDate": "2026-10-02", "endDate": "2026-10-03"})
    keep, _n, _f = rules.conference_ok(future, 0.95, today)
    check("upcoming event kept", keep)
    keep, _n, force = rules.conference_ok(future, 0.65, today)
    check("below conference minimum forced to Lower", keep and force)
    noschema = {"section": "Conference", "url": "https://binaytara.org/projects/conferences/7th-icc-2024",
                "event": None, "h1": "7th ICC 2024", "title": "7th ICC 2024"}
    keep, _n, _f = rules.conference_ok(noschema, 0.95, today)
    check("no schema, past year in slug dropped", not keep)
    unknown = {"section": "Conference", "url": "https://binaytara.org/projects/conferences/x",
               "event": None, "h1": "Summit", "title": "Summit"}
    keep, _n, _f = rules.conference_ok(unknown, 0.95, today)
    check("no schema and no year dropped", not keep)


def test_overlap():
    print("\nTopic overlap")
    src = {"h1": "Kidney Cancer Treatment Options", "title_clean": "", "meta_description": ""}
    tgt = {"h1": "Kidney Cancer Treatment Guidelines", "title_clean": "", "meta_description": ""}
    lvl, _ = cannibalization.assess(src, tgt, "kidney cancer treatment")
    check("anchor matching own keywords flagged High", lvl == "High", lvl)
    far = {"h1": "Nurse Staffing in Rural Clinics", "title_clean": "", "meta_description": ""}
    lvl, _ = cannibalization.assess(src, far, "rural clinics")
    check("unrelated pages not flagged", lvl == "None", lvl)
    check("label is not 'cannibalization'",
          "cannibal" not in cannibalization.LABEL.lower())


def test_misc():
    print("\nScoring, Excel escaping, brand stripping")
    check("band High", rules.band(0.70) == "High")
    check("band Medium", rules.band(0.50) == "Medium")
    check("band below floor dropped", rules.band(0.10) is None)
    check("benchmark short", rules.benchmark(300) == "2 to 4")
    check("benchmark long", rules.benchmark(1800) == "8 to 12")
    lo = rules.final_score(0.8, 0.5, 0.9, deorphan=True, inbound=0)
    hi = rules.final_score(0.8, 0.5, 0.9, deorphan=True, inbound=10)
    check("de-orphan boosts low inbound pages", lo > hi)
    plain = rules.final_score(0.8, 0.5, 0.9)
    synth = rules.final_score(0.8, 0.5, 0.9, synthetic=True)
    check("synthetic chunks penalised", synth < plain)

    for danger in ["=SUM(A1)", "+1", "-1", "@cmd"]:
        check(f"excel escapes {danger[:2]}", excel_writer.esc(danger).startswith("'"))
    check("excel leaves normal text alone", excel_writer.esc("kidney cancer") == "kidney cancer")
    n1 = excel_writer.sheet_name("Give", "https://binaytara.org/a/b")
    n2 = excel_writer.sheet_name("Give", "https://binaytara.org/a/b")
    check("sheet names stable across runs", n1 == n2)
    check("sheet name within Excel limit", len(n1) <= 31, n1)
    check("brand stripped mid-title",
          strip_brand("Kidney Cancer 2025 | Binaytara | GU Cancer") ==
          "Kidney Cancer 2025 | GU Cancer",
          strip_brand("Kidney Cancer 2025 | Binaytara | GU Cancer"))
    check("modified sentence inserts a markdown link",
          "[cancer](https://x)" in rules.modified_sentence(
              "a cancer b", (2, 8), "cancer", "https://x", "Exact in text"))
    check("needs insertion gives an instruction",
          "incorporate" in rules.modified_sentence(
              "a b", None, "kidney cancer", "https://x", "Needs insertion"))


if __name__ == "__main__":
    for fn in (test_blocks, test_urls, test_r7, test_anchor, test_caps,
               test_contributor, test_conference, test_overlap, test_misc):
        fn()
    print("\n" + "=" * 60)
    if FAILURES:
        print(f"{len(FAILURES)} FAILURES: {FAILURES}")
        sys.exit(1)
    print("All tests passed.")
