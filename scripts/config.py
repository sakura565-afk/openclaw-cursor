"""Public source endpoints, patterns, rate limits, and seed dictionaries for ``content_scraper``."""

from __future__ import annotations

import re
from typing import Final

# -----------------------------------------------------------------------------
# Paths (relative to repository root)
# -----------------------------------------------------------------------------

DATA_RAW_DIR: Final[str] = "data/raw"
DATA_PROCESSED_DIR: Final[str] = "data/processed"
DEDUPE_INDEX_NAME: Final[str] = "_dedupe_hashes.json"

# -----------------------------------------------------------------------------
# HTTP
# -----------------------------------------------------------------------------

# Reddit requires a descriptive User-Agent; keep it honest and identify the script.
HTTP_USER_AGENT: Final[str] = (
    "PublicContentScraper/1.0 (public JSON only; +https://github.com/anysphere/local-research)"
)
HTTP_TIMEOUT_TOTAL_SEC: Final[float] = 45.0
HTTP_MAX_REDIRECTS: Final[int] = 5

# -----------------------------------------------------------------------------
# Reddit (public *.json endpoints only; no OAuth, no private APIs)
# -----------------------------------------------------------------------------

REDDIT_BASE: Final[str] = "https://www.reddit.com"
# Subreddit slugs without leading ``r/`` — lowercase. Add/modify Russian or NSFW
# communities that are publicly readable without authentication.
REDDIT_SUBREDDITS: tuple[str, ...] = (
    "pikabu",
    "AskARussian",
    "russia",
)
REDDIT_LISTING: Final[str] = "new"  # new | hot — keep ``new`` for time windows
REDDIT_PER_REQUEST_LIMIT: Final[int] = 100
REDDIT_RATE_LIMIT_DELAY_SEC: Final[float] = 1.35

# Match canonical Reddit discussion URLs we persist (permalink shape).
REDDIT_URL_SLUG_RE: Final[re.Pattern[str]] = re.compile(
    r"^https://(?:www\.|old\.)?reddit\.com/r/[\w-]+/comments/[\w-]+/",
    re.IGNORECASE,
)

# -----------------------------------------------------------------------------
# 4chan ``/ru/`` board — public CDN JSON only (`a.4cdn.org`).
# -----------------------------------------------------------------------------

# 4chan board slug (public CDN JSON). If ``/ru/catalog.json`` returns 404, the slug may be
# retired; set ``FOURCHAN_BOARD_SLUG`` to an active board (for example ``int``).
FOURCHAN_BOARD_SLUG: Final[str] = "ru"
FOURCHAN_CATALOG_URL: Final[str] = f"https://a.4cdn.org/{FOURCHAN_BOARD_SLUG}/catalog.json"
FOURCHAN_THREAD_URL_TEMPLATE: Final[str] = f"https://a.4cdn.org/{FOURCHAN_BOARD_SLUG}/thread/{{no}}.json"
FOURCHAN_THREAD_WEB_BASE: Final[str] = f"https://boards.4chan.org/{FOURCHAN_BOARD_SLUG}/thread"
FOURCHAN_RATE_LIMIT_DELAY_SEC: Final[float] = 0.75
# Catalog pages are batched; cap thread fetches per run for politeness.
FOURCHAN_MAX_THREADS_PER_RUN: Final[int] = 40

FOURCHAN_URL_SLUG_RE: Final[re.Pattern[str]] = re.compile(
    r"^https://(?:boards\.4chan\.org|(?:www\.)?4chan\.org)/"
    + re.escape(FOURCHAN_BOARD_SLUG)
    + r"/thread/\d+",
    re.IGNORECASE,
)

# -----------------------------------------------------------------------------
# Toxicity filter — reject extremely abusive / violent phrasing (basic substring check).
# Extend with domain-specific denylists as needed. Keep literals UTF-8.
# -----------------------------------------------------------------------------

TOXICITY_BLOCK_SUBSTRINGS: tuple[str, ...] = (
    # Russian — extreme threats / degrading fixation on real harm (minimal seed list)
    "убей ",
    "убейте ",
    "зарежу",
    "застрел",
    "сдохни",
    "насильно",
    "изнасило",
    "педофил",
    "детское порно",
    "цп ",
    "цп,",
    # English — common hard-decline patterns on imageboards
    " kill yourself",
    "kys ",
    "kys,",
    "child porn",
    "cp links",
)

# -----------------------------------------------------------------------------
# Russian + mixed internet slang seeds (case-insensitive match)
# -----------------------------------------------------------------------------

SLANG_SEEDS_RU: tuple[str, ...] = (
    "кек",
    "лол",
    "рофл",
    "кринж",
    "кринжов",
    "норм",
    "ненорм",
    "заеб",
    "зашквар",
    "шквар",
    "хайп",
    "хайпов",
    "флекс",
    "флексить",
    "тильт",
    "токсик",
    "токсич",
    "душнил",
    "пикми",
    "сигма",
    "чилл",
    "рил",
    "вайб",
    "имба",
    "кринжа",
    "лут",
    "скилл",
    "скил",
    "пруф",
    "шитпост",
    "мемас",
    "мемчик",
)

SLANG_SEEDS_EN: tuple[str, ...] = (
    "kek",
    "lol",
    "lmao",
    "based",
    "cringe",
    "copium",
    "ratio",
    "npc",
)

# -----------------------------------------------------------------------------
# Loose category buckets (keyword → label). First match wins after toxicity filter.
# -----------------------------------------------------------------------------

_CATEGORY_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("bitcoin", "крипт", "блокчейн", "эфири", "биткоин", "nft "), "crypto_tech"),
    (("docker", "kubernetes", "линукс", "linux", "devops", "код ", "програм"), "tech"),
    (("путин", "кремл", "нато", "война", "ukraine", "украин", "election", "выбор"), "politics"),
    (("дота", "counter-strike", "csgo", "стим", "steam", "видеокарт", "gpu"), "gaming_hw"),
)


def categorize_text(text_lower: str, slang_hit: bool) -> str:
    if slang_hit:
        return "internet_slang"
    for needles, label in _CATEGORY_RULES:
        if any(n in text_lower for n in needles):
            return label
    return "general"


def all_slang_seeds() -> tuple[str, ...]:
    return tuple(sorted(set(SLANG_SEEDS_RU + SLANG_SEEDS_EN), key=len, reverse=True))
