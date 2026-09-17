"""CSS selectors and text patterns used by the extractor. Re-verify against live
HTML before a build; Strapi templates can change."""

BODY_SELECTORS = [
    "article",
    "main article",
    "[class*='article-body']",
    "[class*='post-content']",
    "[class*='articleBody']",
    "main",
]

ABSTRACT_SELECTORS = [
    "[class*='abstract']",
    "[id*='abstract']",
]

# Containers removed from the body before block extraction. Their links must NOT
# count as body links, or R7 would wrongly suppress good suggestions.
JUNK_SELECTORS = [
    "nav", "header", "footer", "aside", "form", "script", "style", "noscript",
    "[class*='recommend']", "[class*='related']", "[class*='more-from']",
    "[class*='breadcrumb']", "[class*='share']", "[class*='subscribe']",
    "[class*='newsletter']", "[class*='author-card']", "[class*='authorCard']",
    "[class*='topics']", "[class*='sidebar']", "[class*='menu']",
    "[class*='cookie']", "[class*='banner']",
]

BLOCK_TAGS = ("p", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "blockquote")

IJCCD_PLACEHOLDER_META = "ijccd article"

KEY_TAKEAWAYS_RE = r"^\s*(key\s+takeaways?|take[\s-]?home\s+messages?|at\s+a\s+glance)\b"
REFERENCES_RE = r"^\s*(works\s+discussed|references|bibliography|citations|source\s+list|works\s+cited)\b"
CAPTION_RE = r"^\s*(figure|image|table|fig\.)\s*\d+[\.\:]"
DISCLAIMER_RE = r"(for\s+informational\s+purposes\s+only|not\s+intended\s+as\s+medical\s+advice)"

# Attribution verbs near a quotation mark signal a direct quote.
ATTRIBUTION_RE = (
    r"\b(he\s+said|she\s+said|they\s+said|said\s+Dr|noted\s+Dr|added\s+Dr|"
    r"explained\s+Dr|according\s+to\s+Dr|told\s+us|he\s+stated|she\s+stated|"
    r"stated|wrote|concluded|remarked)\b"
)

# Credentialled name. Only counts as a quote signal when a quotation mark is
# ALSO present in the block: Binaytara prose names credentialled physicians
# constantly in ordinary narrative sentences.
CREDENTIAL_RE = (
    r"(?:(?:Dr|Prof)\.?\s+)?[A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+,?\s*"
    r"(?:M\.?D|Ph\.?D|D\.?O|M\.?P\.?H|R\.?N|D\.?N\.?B|M\.?B\.?B\.?S)\.?"
)

# U+2019 is used as an apostrophe ("cancer\u2019s") far more often than as a quote
# mark, so it must not be counted on its own. Apostrophes sitting between letters
# are stripped before quote detection; see blocks._strip_apostrophes.
QUOTE_CHARS = '"\u201c\u201d\u2018\u2019'
STRONG_QUOTE_CHARS = '"\u201c\u201d'

# A short paragraph rendered entirely in bold is a heading in the TCN templates,
# which do not use real <h2> tags in article bodies. Without this, Key Takeaways
# and References regions are never detected.
PSEUDO_HEADING_BOLD_COVER = 0.90
PSEUDO_HEADING_MAX_WORDS = 20

# Marketing verbs. Meta descriptions are written to these per SOP 3.1, so
# n-gram extraction over them reliably produces unusable anchors.
SERP_VERBS = {
    "learn", "discover", "explore", "read", "find", "see", "get", "check",
    "understand", "click", "here", "stay", "join", "sign", "subscribe", "watch",
}

BRAND_SUFFIXES = [
    "| The Cancer News", "\u2502 The Cancer News", "| Binaytara", "\u2502 Binaytara",
    "| IJCCD", "\u2502 IJCCD", "- The Cancer News", "- Binaytara",
]

EXTRA_STOPWORDS = {
    "binaytara", "cancer", "news", "article", "study", "report", "new", "latest",
    "using", "used", "may", "one", "two", "also",
}
