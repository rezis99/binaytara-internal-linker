"""Unit tests for the SOP rule engine, the block classifier, keyword scanner,
and LLM rewrite validation.

v3 additions: keyword_scan tests, title_similarity tests, scoring with keyword
weight, LLM validation tests.
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import url_rules as ur                              # noqa: E402
from engine import anchor, cannibalization, conference, rules    # noqa: E402
from engine import keyword_scan                                  # noqa: E402
from engine import cannibalization_data                          # noqa: E402
from indexer.extractor import h1_is_consistent                   # noqa: E402
from engine import llm_rewrite                                   # noqa: E402
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
    q = "https://binaytara.org/cancernews/foo?utm_source=x"
    f = "https://binaytara.org/cancernews/foo#section"
    check("is_allowed canonicalises a query string", ur.is_allowed(q))
    check("is_allowed canonicalises a fragment", ur.is_allowed(f))
    check("normalise strips the query",
          ur.normalise(q) == "https://binaytara.org/cancernews/foo", ur.normalise(q))
    check("normalise strips the fragment",
          ur.normalise(f) == "https://binaytara.org/cancernews/foo", ur.normalise(f))
    check("url_ok rejects the query string outright", not rules.url_ok(q))
    check("url_ok rejects the fragment outright", not rules.url_ok(f))
    check("url_ok accepts the canonicalised form", rules.url_ok(ur.normalise(q)))
    check("url_ok rejects http", not rules.url_ok("http://binaytara.org/x"))
    check("draft placeholder never shown as a URL",
          "draft://" not in rules.display_url("draft://x.docx"))


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
    keep, _n, force = rules.conference_ok(future, 0.55, today)
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


def test_keyword_scan():
    print("\nKeyword scan (v3)")

    # Term extraction.
    article = {
        "h1": "Alcohol and Stomach Cancer Risk",
        "title_clean": "Alcohol and Stomach Cancer Risk",
        "blocks": [
            {"kind": "h2", "text": "Risk Factors"},
            {"kind": "p", "text": "Heavy alcohol consumption is a major risk factor "
                                   "for stomach cancer and esophageal cancer."},
            {"kind": "p", "text": "Smoking combined with alcohol use increases the "
                                   "risk of pancreatic cancer significantly."},
        ],
    }
    terms = keyword_scan.extract_terms(article)
    check("extracts H1 terms",
          any("stomach" in t and "cancer" in t for t in terms) or
          any("alcohol" in t for t in terms), terms[:10])
    check("terms list is not empty", len(terms) > 0, terms)

    # Body text scanning.
    body_texts = {
        "https://binaytara.org/cancernews/article/stomach-cancer-101":
            "stomach cancer is a disease that affects the stomach lining. "
            "alcohol consumption is a known risk factor for this condition.",
        "https://binaytara.org/cancernews/article/kidney-cancer-awareness":
            "kidney cancer affects hundreds of thousands of people worldwide. "
            "early detection improves survival rates significantly.",
        "https://binaytara.org/cancernews/article/esophageal-cancer-risks":
            "esophageal cancer has several risk factors including alcohol "
            "use and tobacco smoking over extended periods.",
    }
    scores = keyword_scan.scan_pages(terms, body_texts, set())
    check("stomach cancer page found by keyword scan",
          "https://binaytara.org/cancernews/article/stomach-cancer-101" in scores,
          scores)
    check("esophageal cancer page found",
          "https://binaytara.org/cancernews/article/esophageal-cancer-risks" in scores,
          scores)
    # Kidney cancer page should score lower (doesn't mention alcohol or stomach).
    stomach_score = scores.get("https://binaytara.org/cancernews/article/stomach-cancer-101", 0)
    kidney_score = scores.get("https://binaytara.org/cancernews/article/kidney-cancer-awareness", 0)
    check("stomach cancer scores higher than kidney cancer for alcohol article",
          stomach_score > kidney_score,
          f"stomach={stomach_score:.2f} kidney={kidney_score:.2f}")


def test_title_similarity():
    print("\nTitle similarity (v3)")
    source = {"h1": "Stomach Cancer Symptoms and Risk Factors",
              "title_clean": "Stomach Cancer Symptoms"}
    pages = {
        "https://binaytara.org/a": {"h1": "Stomach Cancer Treatment Options",
                                     "title_clean": "Stomach Cancer Treatment"},
        "https://binaytara.org/b": {"h1": "Kidney Cancer Awareness Month",
                                     "title_clean": "Kidney Cancer Awareness"},
        "https://binaytara.org/c": {"h1": "Gastric Cancer Early Detection",
                                     "title_clean": "Gastric Cancer Detection"},
    }
    sims = keyword_scan.title_similarity(source, pages, set())
    check("stomach cancer article found as same-topic",
          "https://binaytara.org/a" in sims, sims)
    check("kidney cancer NOT flagged as same-topic (different disease)",
          "https://binaytara.org/b" not in sims, sims)


def test_llm_validation():
    print("\nLLM rewrite validation (v3)")
    # Good rewrite: anchor appears verbatim.
    check("valid rewrite accepted",
          llm_rewrite._validate(
              "Alcohol is a risk factor for many cancers.",
              "Alcohol is a well-known risk factor for stomach cancer and many other cancers.",
              "stomach cancer"))
    # Bad rewrite: anchor missing.
    check("rewrite without anchor rejected",
          not llm_rewrite._validate(
              "Alcohol is a risk factor.",
              "Alcohol is a risk factor for many types of malignancies.",
              "stomach cancer"))
    # Bad rewrite: too long.
    check("overlong rewrite rejected",
          not llm_rewrite._validate(
              "Short.",
              "This is an extremely long rewrite " * 20,
              "stomach cancer"))
    # Bad rewrite: contains markdown.
    check("rewrite with markdown rejected",
          not llm_rewrite._validate(
              "Alcohol is a risk factor.",
              "Alcohol is a risk factor for [stomach cancer](url).",
              "stomach cancer"))


def test_scoring_v3():
    print("\nv3 scoring with keyword weight")
    # A page with a keyword hit should score higher than one without.
    s_no_kw = rules.final_score(0.3, 0.2, 0.9, keyword_score=0.0)
    s_with_kw = rules.final_score(0.3, 0.2, 0.9, keyword_score=0.5)
    check("keyword score boosts total", s_with_kw > s_no_kw,
          f"kw={s_with_kw:.3f} no_kw={s_no_kw:.3f}")

    # Keyword hit floor: a strong keyword hit rescues a weak embedding score.
    s_rescued = rules.final_score(0.1, 0.1, 0.9, keyword_score=0.4)
    check("keyword hit floor rescues weak embedding",
          s_rescued >= 0.40, f"score={s_rescued:.3f}")

    # Title similarity floor.
    from config import settings
    s_title = rules.final_score(0.1, 0.1, 0.9, title_sim=0.50)
    check("title similarity floor applied",
          s_title >= settings.TITLE_SIMILARITY_FLOOR * 0.9, f"score={s_title:.3f}")


def test_misc():
    print("\nScoring, Excel escaping, brand stripping")
    check("band High", rules.band(0.60) == "High")
    check("band Medium", rules.band(0.42) == "Medium")
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
    check("needs insertion with LLM rewrite uses the rewrite",
          "already rewritten" in rules.modified_sentence(
              "a b", None, "kidney cancer", "https://x", "Needs insertion",
              llm_rewrite="already rewritten with [kidney cancer](https://x)"))


def test_h1_cms_guard():
    """v4: the CMS serves a foreign H1 on roughly 10% of TCN articles. These are
    real cases measured against the live site in September 2026."""
    print("\nH1 CMS defect guard (v4)")
    bad = [
        ("Reaching Out-of-School Maasai Girls With HPV Vaccination: Cervical Cancer Prevention in Tanzania",
         "Kidney Cancer in 2026: Immunotherapy, HIF-2 Alpha, TKIs",
         "https://binaytara.org/cancernews/article/kidney-cancer-dual-io-hif2a-tki-free-intervals",
         "Maasai H1 on the kidney cancer page"),
        ("AL Amyloidosis, POEMS Syndrome, and Extramedullary Myeloma",
         "Colorectal Cancer: Exercise, Aspirin, and Immunotherapy",
         "https://binaytara.org/cancernews/article/colorectal-cancer-exercise-aspirin-immunotherapy",
         "amyloidosis H1 on the colorectal page"),
        ("Dr. David Zhen on Advances in Neuroendocrine Tumor Treatment",
         "Multi-Omics & AI in Precision Health: Dr. Michael Snyder",
         "https://binaytara.org/cancernews/article/dr-michael-snyder-on-advances-in-genomic-medicine",
         "wrong physician H1"),
    ]
    for h1, title, url, label in bad:
        ok, _ = h1_is_consistent(h1, title, url)
        check(f"detects {label}", not ok)

    good = [
        ("Stomach Cancer 101", "Stomach Cancer 101: Symptoms, Causes and Treatment",
         "https://binaytara.org/cancernews/article/stomach-cancer-101", "matching H1"),
        ("Alcohol and Cancer Risk: What the Evidence Shows",
         "Alcohol and Cancer Risk | The Cancer News",
         "https://binaytara.org/cancernews/article/alcohol-cancer-risk", "alcohol article"),
        ("", "Some Title", "https://binaytara.org/x/y", "empty H1"),
        ("Belzutifan in 2026: Three Phase 3 Trials",
         "Belzutifan in 2026: Three Phase 3 Trials",
         "https://binaytara.org/cancernews/article/belzutifan-in-2026", "short slug"),
    ]
    for h1, title, url, label in good:
        ok, _ = h1_is_consistent(h1, title, url)
        check(f"does not flag {label}", ok)


def test_real_cannibalization():
    print("\nReal keyword cannibalization (v4)")
    iver_a = "https://binaytara.org/cancernews/article/caution-about-ivermectin-for-cancer-treatment-from-an-oncologist"
    iver_b = "https://binaytara.org/cancernews/article/what-cancer-doctors-are-saying-about-ivermectin-and-cancer-treatment"
    cmap = {
        iver_a: {iver_b: {"shared": 228, "volume": 43170,
                          "top_terms": ["ivermectin for cancer", "ivermectin and cancer"]}},
        iver_b: {iver_a: {"shared": 228, "volume": 43170,
                          "top_terms": ["ivermectin for cancer", "ivermectin and cancer"]}},
    }
    lvl, why = cannibalization_data.assess(iver_a, iver_b, cmap)
    check("228 shared queries flagged High", lvl == "High", lvl)
    check("explanation names the query count", "228" in why, why)
    lvl, _ = cannibalization_data.assess(iver_a, "https://binaytara.org/unrelated", cmap)
    check("uncompeting pair returns None", lvl == "None", lvl)
    lvl, _ = cannibalization_data.assess(iver_a, iver_b, {})
    check("empty map returns no verdict", lvl == "", lvl)

    src = {"url": iver_a, "h1": "Ivermectin Caution", "title_clean": "", "meta_description": ""}
    tgt = {"url": iver_b, "h1": "What Doctors Say", "title_clean": "", "meta_description": ""}
    lvl, why, basis = rules.overlap(src, tgt, "ivermectin dosage", cmap)
    check("rules.overlap reports query-data basis", basis == "query data", basis)
    lvl2, why2, basis2 = rules.overlap(src, tgt, "ivermectin dosage", {})
    check("falls back to the labelled heuristic", basis2 == "title heuristic", basis2)


def test_awareness_matching():
    print("\nAwareness and conference matching (v4)")
    art = {"h1": "Kidney Cancer in 2026: Immunotherapy, HIF-2 Alpha, TKIs",
           "title_clean": "Kidney Cancer in 2026",
           "blocks": [{"kind": "p", "text": "Kidney cancer treatment advanced fast."}]}
    check("article disease detected", "kidney cancer" in keyword_scan.article_diseases(art))

    pages = {
        "https://binaytara.org/cancernews/article/kidney-cancer-awareness-month":
            {"h1": "Kidney Cancer Awareness Month", "title_clean": "Kidney Cancer Awareness Month",
             "section": "TCN", "url": "https://binaytara.org/cancernews/article/kidney-cancer-awareness-month"},
        "https://binaytara.org/cancernews/article/breast-cancer-101":
            {"h1": "Breast Cancer 101", "title_clean": "Breast Cancer 101",
             "section": "TCN", "url": "https://binaytara.org/cancernews/article/breast-cancer-101"},
        "https://binaytara.org/projects/conferences/gu-2026":
            {"h1": "GU Cancers Summit: kidney cancer sessions", "title_clean": "GU Cancers Summit",
             "section": "Conference", "url": "https://binaytara.org/projects/conferences/gu-2026"},
    }
    m = keyword_scan.awareness_and_conference_matches(art, pages, set())
    check("Kidney Cancer Awareness Month found (review item #6)",
          "https://binaytara.org/cancernews/article/kidney-cancer-awareness-month" in m, list(m))
    check("conference recap matched on disease (review item #7)",
          "https://binaytara.org/projects/conferences/gu-2026" in m, list(m))
    check("unrelated disease page not matched",
          "https://binaytara.org/cancernews/article/breast-cancer-101" not in m, list(m))

    # A specific disease must not also drag in the generic parent term.
    art2 = {"h1": "Stomach Cancer 101", "title_clean": "Stomach Cancer 101", "blocks": []}
    d = keyword_scan.article_diseases(art2)
    check("specific disease term wins", d == {"stomach cancer"}, d)


def test_blocked_anchors_v4():
    print("\nGeneric and geographic anchor blocklist (v4)")
    for bad in ["all cancer", "cancer awareness", "risk factors", "early detection"]:
        check(f"'{bad}' earns no keyword evidence",
              rules.keyword_evidence(2, "Exact in text", bad) == 0.0)
    for geo in ["north india", "tanzania", "united states", "new york"]:
        check(f"geographic anchor '{geo}' earns no evidence",
              rules.keyword_evidence(2, "Exact in text", geo) == 0.0)
    check("a real topical anchor still earns evidence",
          rules.keyword_evidence(2, "Exact in text", "stomach cancer") > 0)


def test_extra_floor():
    print("\nAwareness score floor (v4)")
    base = rules.final_score(0.1, 0.1, 0.9)
    lifted = rules.final_score(0.1, 0.1, 0.9, extra_floor=0.60)
    check("awareness floor lifts a weak embedding match", lifted > base,
          f"{lifted:.3f} vs {base:.3f}")
    check("awareness floor reaches at least Medium",
          rules.band(lifted) in ("High", "Medium"), rules.band(lifted))


if __name__ == "__main__":
    for fn in (test_blocks, test_urls, test_r7, test_anchor, test_caps,
               test_contributor, test_conference, test_overlap,
               test_keyword_scan, test_title_similarity, test_llm_validation,
               test_scoring_v3, test_h1_cms_guard, test_real_cannibalization,
               test_awareness_matching, test_blocked_anchors_v4,
               test_extra_floor, test_misc):
        fn()
    print("\n" + "=" * 60)
    if FAILURES:
        print(f"{len(FAILURES)} FAILURES: {FAILURES}")
        sys.exit(1)
    print("All tests passed.")
