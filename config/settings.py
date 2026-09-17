"""All tunable values live here. Nothing else in the codebase hard-codes a threshold."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# ---------- Site ----------
SITE = "https://binaytara.org"
SITEMAP_URL = f"{SITE}/sitemap.xml"
ALLOWED_HOST = "binaytara.org"

# ---------- Embedding contract ----------
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
USER_AGENT = "BinaytaraInternalLinker/3.0 (SEO tooling; contact rejish.s@binaytara.org)"

# ---------- Chunking ----------
MIN_BLOCK_WORDS = 20
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
# v3 RECALIBRATION. v2 measured unrelated p95 at cosine 0.670 and set the hard
# gate at 0.70, which was above p99 (0.719). That killed recall on the full
# 1,680-page index: the same article went from 26 suggestions (v1, too many) to
# 2 (v2, too few). The v3 audit target is 8 to 15 suggestions per article with
# 60%+ precision.
#
# The floor is lowered to p75 of the unrelated distribution. Genuine matches
# still separate clearly above 0.72, but near-misses that keyword evidence or
# title similarity can rescue are no longer silently dropped.
COSINE_NOISE_FLOOR = 0.60
COSINE_SIGNAL_CEIL = 0.82

# Hard gate: below this, a candidate is noise regardless of keyword evidence.
COSINE_HARD_MIN = 0.55

# "Needs insertion" still needs stronger evidence than "Exact in text".
COSINE_INSERTION_MIN = 0.68

W_SEMANTIC = 0.45
W_LEXICAL = 0.20
W_ANCHOR = 0.15
W_KEYWORD = 0.20        # NEW: weight for the keyword-scan signal

ANCHOR_TIER_SCORE = {1: 1.00, 2: 0.90, 3: 0.75, 4: 0.60, 5: 0.35}

MATCH_MULTIPLIER = {
    "Exact in text": 1.00,
    "Synonym in text": 0.95,
    "Partial in text": 0.80,
    "Needs insertion": 0.60,
}

KEYWORD_EVIDENCE_FLOOR = 0.62
KEYWORD_EVIDENCE_MIN_WORDS = 2

SYNTHETIC_PENALTY = 0.80

BAND_HIGH = 0.55
BAND_MEDIUM = 0.38
BAND_MIN = 0.22
CONFERENCE_MIN_SCORE = 0.60

# ---------- SOP rules ----------
MAX_PER_PARAGRAPH = 2
ANCHOR_MIN_WORDS = 2
ANCHOR_MAX_WORDS = 5
CROWDED_PAGE_LINKS = 12

LINK_BENCHMARK = [(500, "2 to 4"), (1000, "4 to 8"), (10 ** 9, "8 to 12")]

# ---------- Topic overlap ----------
OVERLAP_HIGH = 0.60
OVERLAP_MEDIUM = 0.35

# ---------- Keyword scan (v3) ----------
# Minimum term length and occurrence thresholds for the keyword scanner.
KEYWORD_MIN_TERM_LEN = 4          # ignore very short terms
KEYWORD_MAX_TERMS = 30            # cap extracted terms per article
# A page-level keyword hit on a core disease term is strong independent evidence.
# It sets a floor under the combined score, rescuing candidates the embedding
# missed (the "alcohol" <> "stomach cancer" failure from the v2 audit).
KEYWORD_HIT_FLOOR = 0.50
# Title similarity threshold for same-topic detection. Two articles whose H1s
# share more than this fraction of content words are considered same-topic and
# always surface as candidates.
TITLE_SIMILARITY_MIN = 0.40
TITLE_SIMILARITY_FLOOR = 0.58     # score floor for same-topic matches

# ---------- LLM rewriting (v3) ----------
LLM_ENABLED = True
LLM_PROVIDER = "groq"             # "groq" | "huggingface" | "none"
LLM_MODEL_GROQ = "llama-3.1-8b-instant"
LLM_MODEL_HF = "mistralai/Mistral-7B-Instruct-v0.3"
LLM_TIMEOUT = 15.0
LLM_MAX_RETRIES = 2

# ---------- De-orphaning ----------
DEORPHAN_BONUS = 0.10
DEORPHAN_CAP = 10

# ---------- App ----------
BATCH_WORKERS = 1
BATCH_MAX = 20
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
INDEX_STALE_DAYS = 10

ANCHOR_GUIDE_CSV = (
    "https://docs.google.com/spreadsheets/d/"
    "1SiecxT_ZffUJiVHXRgswaKeB5HtLxeZGsTo3QXd8z6Q/export?format=csv&gid=84068859"
)
