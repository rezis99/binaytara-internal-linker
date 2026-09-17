"""All tunable values live here. Nothing else in the codebase hard-codes a threshold."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# ---------- Site ----------
SITE = "https://binaytara.org"
SITEMAP_URL = f"{SITE}/sitemap.xml"
ALLOWED_HOST = "binaytara.org"

# ---------- Embedding contract ----------
# Verified empirically on fastembed 0.4.2: query_embed() does NOT apply the BGE
# instruction prefix (it returns vectors identical to embed()). The application
# must therefore add it itself. Vectors come back L2-normalised, so FAISS inner
# product equals cosine similarity.
MODEL_NAME = "BAAI/bge-small-en-v1.5"
EMBED_DIM = 384
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
QUERY_PREFIX_APPLIED_BY = "application"
EMBED_BATCH = 32

# ---------- Crawl ----------
MAX_CONCURRENCY = 25
REQUEST_TIMEOUT = 20.0
MAX_RETRIES = 3
MAX_REDIRECTS = 5
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
USER_AGENT = "BinaytaraInternalLinker/1.0 (SEO tooling; contact rejish.s@binaytara.org)"

# ---------- Chunking ----------
MIN_BLOCK_WORDS = 20        # below this a block is a caption or one-liner, not prose
MAX_CHUNK_WORDS = 120
MIN_BODY_CHARS = 500
MAX_LINK_TEXT_RATIO = 0.5

# ---------- Retrieval ----------
DENSE_K = 30
LEXICAL_K = 30
RRF_K = 60
CANDIDATES_PER_CHUNK = 10
RECEIVE_SOURCE_PAGES = 15

# ---------- Scoring ----------
# MEASURED, not assumed. Cosine similarity between 778 pairs of UNRELATED chunks
# from this corpus (bge-small-en-v1.5): p1 0.399, p25 0.508, median 0.563,
# p95 0.670, p99 0.719. BGE similarities live in a narrow high band, so the
# naive (cos + 1) / 2 mapping gave two unrelated paragraphs a semantic score of
# 0.78 and pushed almost every candidate into the Medium band. Rescale against
# the observed noise floor instead: everything at or below the unrelated p95
# scores zero, and only genuinely close matches earn signal.
COSINE_NOISE_FLOOR = 0.70     # just above unrelated p99 (0.719 measured)
COSINE_SIGNAL_CEIL = 0.82     # measured: genuinely good matches top out near here

# Hard gate applied before scoring. A candidate below this is not a weak
# suggestion, it is noise, and no anchor quality should rescue it.
COSINE_HARD_MIN = 0.70

# "Needs insertion" asks the writer to rewrite a sentence. That is only worth
# their time when the topical match is strong, so it carries a higher bar.
COSINE_INSERTION_MIN = 0.80

W_SEMANTIC = 0.55
W_LEXICAL = 0.25
W_ANCHOR = 0.20

ANCHOR_TIER_SCORE = {1: 1.00, 2: 0.90, 3: 0.75, 4: 0.60, 5: 0.35}

# PRIMARY CALIBRATION PARAMETER. Sweep 0.55 to 0.85 against the golden set.
MATCH_MULTIPLIER = {
    "Exact in text": 1.00,
    "Synonym in text": 0.95,
    "Partial in text": 0.80,
    "Needs insertion": 0.60,
}

# Anchor quality measures how good the ANCHOR is, not how relevant the TARGET
# is, so it scales the relevance score rather than adding to it: it can only
# discount a match, never rescue an irrelevant one.
#
# But an exact match on a SPECIFIC multi-word phrase taken from the target's own
# H1 is not merely good anchor text, it is independent evidence that the two
# pages share a topic. "breast cancer" appearing verbatim in a paragraph about
# breast cancer risk, pointing at a page whose H1 is about breast cancer, is a
# good link whatever the embedding says. Embeddings compress hard on
# domain-specific corpora, so this lexical evidence is treated as a floor on
# relevance rather than being discarded.
KEYWORD_EVIDENCE_FLOOR = 0.62      # applies to Tier 1 and 2 exact/synonym matches
KEYWORD_EVIDENCE_MIN_WORDS = 2

# A hub or listing page is represented by ONE synthetic chunk built from its H1,
# title and meta description. That text is short and keyword-dense, which gives
# it an unfair advantage in both dense and lexical retrieval against real
# paragraphs. Penalise it so a real article beats a hub page at equal relevance.
SYNTHETIC_PENALTY = 0.80

BAND_HIGH = 0.62
BAND_MEDIUM = 0.45
BAND_MIN = 0.30
CONFERENCE_MIN_SCORE = 0.70

# ---------- SOP rules ----------
MAX_PER_PARAGRAPH = 2
ANCHOR_MIN_WORDS = 2
ANCHOR_MAX_WORDS = 5
CROWDED_PAGE_LINKS = 12

LINK_BENCHMARK = [(500, "2 to 4"), (1000, "4 to 8"), (10 ** 9, "8 to 12")]

# ---------- Topic overlap ----------
OVERLAP_HIGH = 0.60
OVERLAP_MEDIUM = 0.35

# ---------- De-orphaning ----------
DEORPHAN_BONUS = 0.10
DEORPHAN_CAP = 10

# ---------- App ----------
BATCH_WORKERS = 1          # raised only after the production memory test passes
BATCH_MAX = 20
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
INDEX_STALE_DAYS = 10

ANCHOR_GUIDE_CSV = (
    "https://docs.google.com/spreadsheets/d/"
    "1SiecxT_ZffUJiVHXRgswaKeB5HtLxeZGsTo3QXd8z6Q/export?format=csv&gid=84068859"
)
