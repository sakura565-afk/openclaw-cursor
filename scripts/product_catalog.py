#!/usr/bin/env python3
"""Product catalog parser for amadey.ru + divaninfo.ru.

Walks the public catalog of a configured site via ``sitemap.xml``, extracts
product cards, persists them into a versioned SQLite database, computes diffs
between runs and can export the current catalog to CSV.

The pipeline is intentionally read-only against the live sites: it performs
GET requests only, respects ``robots.txt`` and applies a polite crawl delay.

CLI::

    python product_catalog.py parse  --site amadey [--dry-run] [--max-pages N] [--json]
    python product_catalog.py diff   --site amadey [--since-run N] [--json]
    python product_catalog.py export --site amadey --out products_amadey_<date>.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

import requests
import yaml
from bs4 import BeautifulSoup

# --------------------------------------------------------------------------- #
# Paths & constants
# --------------------------------------------------------------------------- #

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "sites.yaml"
DEFAULT_DB_PATH = REPO_ROOT / "data" / "product_catalog.db"
LOGS_DIR = REPO_ROOT / "logs"

DEFAULT_USER_AGENT = "OpenClawCatalogBot/1.0 (+https://openclaw.ai/bot)"
REQUEST_TIMEOUT = 15
MAX_WORKERS = 3

_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


# --------------------------------------------------------------------------- #
# Data models
# --------------------------------------------------------------------------- #


@dataclass
class ProductCard:
    """A single product extracted from a detail page."""

    sku: str
    title: str
    category: str | None
    price_rub: float | None
    in_stock: bool | None
    description_short: str | None
    image_main_url: str | None
    image_count: int
    url: str
    fetched_at: str

    def is_complete(self) -> bool:
        """A card is usable only if it has an SKU, a title and a price."""
        return bool(self.sku) and bool(self.title) and self.price_rub is not None


@dataclass
class RunSummary:
    site: str
    started_at: str
    finished_at: str | None = None
    urls_discovered: int = 0
    urls_fetched_ok: int = 0
    urls_failed: int = 0
    added: int = 0
    removed: int = 0
    changed: int = 0
    unchanged: int = 0
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DiffReport:
    site: str
    prev_run_id: int | None
    curr_run_id: int | None
    added: list[dict] = field(default_factory=list)
    removed: list[dict] = field(default_factory=list)
    changed: list[dict] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Config loading
# --------------------------------------------------------------------------- #


class SiteConfig:
    """Typed view over a single site's YAML config block."""

    def __init__(self, slug: str, data: dict):
        self.slug = slug
        self.base_url = data["base_url"].rstrip("/")
        self.product_url_regex = re.compile(data["product_url_regex"])
        category_regex = data.get("category_url_regex")
        self.category_url_regex = re.compile(category_regex) if category_regex else None
        self.user_agent = data.get("user_agent", DEFAULT_USER_AGENT)
        self.selectors: dict[str, list[str]] = data.get("selectors", {})
        # Optional best-effort fallbacks for legacy pages without microdata.
        price_regex = data.get("price_text_regex")
        self.price_text_regex = re.compile(price_regex, re.IGNORECASE) if price_regex else None
        self.sku_url_param = data.get("sku_url_param")


@lru_cache(maxsize=None)
def _load_config_file(config_path: str) -> dict:
    with open(config_path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_site_config(site: str, config_path: Path | str = DEFAULT_CONFIG_PATH) -> SiteConfig:
    """Load a :class:`SiteConfig` for ``site`` from the YAML config file."""
    data = _load_config_file(str(config_path))
    if site not in data:
        available = ", ".join(sorted(data)) or "<none>"
        raise KeyError(f"Unknown site '{site}'. Configured sites: {available}")
    return SiteConfig(site, data[site])


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #


def _build_logger(site: str, run_id) -> logging.Logger:
    """Create a per-run structured logger writing to ``logs/``."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"product_catalog.{site}.{run_id}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    # Avoid duplicate handlers if a logger name is reused within a process.
    for existing in list(logger.handlers):
        logger.removeHandler(existing)
    log_path = LOGS_DIR / f"product_catalog_{site}_{run_id}.log"
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s\t%(levelname)s\t%(message)s"))
    logger.addHandler(handler)
    return logger


def _log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    *,
    url: str = "",
    latency_ms: float | None = None,
    status: str | int = "",
    error: str = "",
) -> None:
    """Emit a single structured log line (tab-separated key=value pairs)."""
    latency = "" if latency_ms is None else f"{latency_ms:.1f}"
    message = (
        f"event={event}\turl={url}\tlatency_ms={latency}\t"
        f"status={status}\terror={error}"
    )
    logger.log(level, message)


# --------------------------------------------------------------------------- #
# Field extraction helpers (pure functions)
# --------------------------------------------------------------------------- #

_PRICE_RE = re.compile(r"[\d][\d\s\u00a0.,]*")
_IN_STOCK_TRUE = ("в наличии", "есть", "in stock", "instock", "available", "на складе")
_IN_STOCK_FALSE = ("нет в наличии", "под заказ", "нет на складе", "out of stock", "outofstock", "sold")


def parse_price(text: str | None) -> float | None:
    """Parse a rouble price string like ``"12 990 ₽"`` into a float."""
    if not text:
        return None
    match = _PRICE_RE.search(text)
    if not match:
        return None
    raw = match.group(0)
    raw = raw.replace("\u00a0", "").replace(" ", "")
    # Treat a comma as decimal separator only when it looks like one (2 digits).
    if "," in raw and "." not in raw:
        if re.search(r",\d{1,2}$", raw):
            raw = raw.replace(",", ".")
        else:
            raw = raw.replace(",", "")
    else:
        raw = raw.replace(",", "")
    try:
        return float(raw)
    except ValueError:
        return None


def parse_in_stock(text: str | None) -> bool | None:
    """Interpret an availability string/attribute into a tri-state boolean."""
    if not text:
        return None
    lowered = text.strip().lower()
    for token in _IN_STOCK_FALSE:
        if token in lowered:
            return False
    for token in _IN_STOCK_TRUE:
        if token in lowered:
            return True
    return None


def _select_first(soup: BeautifulSoup, selectors: Iterable[str]):
    for selector in selectors:
        element = soup.select_one(selector)
        if element is not None:
            return element
    return None


def _element_value(element) -> str | None:
    """Best-effort text/attribute value from a matched element."""
    if element is None:
        return None
    for attr in ("content", "value"):
        if element.has_attr(attr) and element.get(attr):
            return str(element.get(attr)).strip()
    text = element.get_text(" ", strip=True)
    return text or None


def _image_url(element, base_url: str) -> str | None:
    if element is None:
        return None
    for attr in ("content", "src", "data-src", "data-original", "href"):
        if element.has_attr(attr) and element.get(attr):
            return urljoin(base_url + "/", str(element.get(attr)).strip())
    return None


def _count_images(soup: BeautifulSoup, selectors: Iterable[str], base_url: str) -> int:
    urls: set[str] = set()
    for selector in selectors:
        for element in soup.select(selector):
            url = _image_url(element, base_url)
            if url:
                urls.add(url)
    return len(urls)


def _url_param(url: str, param: str) -> str | None:
    """Return a query-string parameter value from ``url`` (or ``None``)."""
    from urllib.parse import parse_qs

    values = parse_qs(urlparse(url).query).get(param)
    return values[0] if values else None


def _category_from_url(url: str, site_config: SiteConfig) -> str | None:
    """Infer a category slug from the product URL path."""
    path = urlparse(url).path.strip("/")
    parts = [p for p in path.split("/") if p]
    if not parts:
        return None
    # amadey: /catalog/<category>/<product>
    if len(parts) >= 3 and parts[0] == "catalog":
        return parts[1]
    # divaninfo: /divan/<product> -> group everything under "divan"
    if len(parts) >= 1 and parts[0] in {"divan", "catalog", "product"}:
        return parts[0]
    # Legacy query-string pages (base.php?id=...) carry no path category.
    if parts[0].endswith(".php"):
        return None
    return parts[0]


def extract_card(soup: BeautifulSoup, url: str, site) -> ProductCard:
    """Pure function: turn a parsed product page into a :class:`ProductCard`.

    ``site`` may be a site slug (loaded from config) or a :class:`SiteConfig`.
    """
    site_config = site if isinstance(site, SiteConfig) else load_site_config(site)
    selectors = site_config.selectors

    sku = _element_value(_select_first(soup, selectors.get("sku", [])))
    if not sku and site_config.sku_url_param:
        sku = _url_param(url, site_config.sku_url_param)
    title = _element_value(_select_first(soup, selectors.get("title", [])))
    price_text = _element_value(_select_first(soup, selectors.get("price", [])))
    stock_element = _select_first(soup, selectors.get("in_stock", []))
    stock_text = None
    if stock_element is not None:
        # itemprop=availability often carries the meaningful value in content.
        stock_text = stock_element.get("content") or stock_element.get_text(" ", strip=True)
    description = _element_value(_select_first(soup, selectors.get("description", [])))
    image_element = _select_first(soup, selectors.get("image_main", []))
    image_main_url = _image_url(image_element, site_config.base_url)
    image_count = _count_images(soup, selectors.get("image_main", []), site_config.base_url)

    price_rub = parse_price(price_text)
    if price_rub is None and site_config.price_text_regex is not None:
        page_text = soup.get_text(" ", strip=True)
        match = site_config.price_text_regex.search(page_text)
        if match:
            price_rub = parse_price(match.group(1) if match.groups() else match.group(0))

    category = _element_value(_select_first(soup, selectors.get("category", [])))
    if not category:
        category = _category_from_url(url, site_config)

    if description and len(description) > 500:
        description = description[:497].rstrip() + "..."

    return ProductCard(
        sku=(sku or "").strip(),
        title=(title or "").strip(),
        category=category,
        price_rub=price_rub,
        in_stock=parse_in_stock(stock_text),
        description_short=description,
        image_main_url=image_main_url,
        image_count=image_count,
        url=url,
        fetched_at=_utcnow(),
    )


# --------------------------------------------------------------------------- #
# Database schema
# --------------------------------------------------------------------------- #

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    urls_discovered INTEGER DEFAULT 0,
    urls_fetched_ok INTEGER DEFAULT 0,
    urls_failed INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT,
    site TEXT NOT NULL,
    url TEXT UNIQUE,
    title TEXT,
    category TEXT,
    price_rub REAL,
    in_stock INTEGER,
    description_short TEXT,
    image_main_url TEXT,
    image_count INTEGER,
    first_seen_run_id INTEGER,
    last_seen_run_id INTEGER,
    is_current INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS product_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT,
    site TEXT NOT NULL,
    run_id INTEGER,
    price_rub REAL,
    in_stock INTEGER,
    title TEXT,
    captured_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_products_site_sku ON products(site, sku);
CREATE INDEX IF NOT EXISTS idx_products_site_current ON products(site, is_current);
CREATE INDEX IF NOT EXISTS idx_history_site_sku_run ON product_history(site, sku, run_id);
"""


def init_db(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open (creating if needed) the SQLite database and ensure the schema."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


# --------------------------------------------------------------------------- #
# Utilities
# --------------------------------------------------------------------------- #


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RobotsPolicy:
    """Minimal robots.txt parser (Disallow rules for our user agent / '*')."""

    def __init__(self, disallowed: list[str]):
        self._disallowed = disallowed

    @classmethod
    def from_text(cls, text: str, user_agent: str) -> "RobotsPolicy":
        disallowed: list[str] = []
        applies = False
        ua_token = user_agent.split("/")[0].lower()
        for line in text.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if key == "user-agent":
                agent = value.lower()
                applies = agent == "*" or agent in ua_token or ua_token in agent
            elif key == "disallow" and applies and value:
                disallowed.append(value)
        return cls(disallowed)

    def allowed(self, url: str) -> bool:
        path = urlparse(url).path or "/"
        return not any(path.startswith(rule) for rule in self._disallowed)


# --------------------------------------------------------------------------- #
# Main pipeline
# --------------------------------------------------------------------------- #


class ProductCatalogParser:
    """Discover, fetch, persist and diff a site's product catalog."""

    def __init__(
        self,
        site: str,
        db_path: Path = DEFAULT_DB_PATH,
        *,
        dry_run: bool = False,
        max_pages: int = 5000,
        delay_s: float = 1.0,
        config_path: Path | str = DEFAULT_CONFIG_PATH,
        session: requests.Session | None = None,
    ):
        self.site = site
        self.db_path = Path(db_path)
        self.dry_run = dry_run
        self.max_pages = max_pages
        self.delay_s = delay_s
        self.config = load_site_config(site, config_path)
        self.session = session or self._build_session()
        self._robots: RobotsPolicy | None = None
        self._rate_lock = threading.Lock()
        self._last_request_ts = 0.0
        self.logger: logging.Logger | None = None
        self.last_discovered: list[str] = []

    # -- HTTP ------------------------------------------------------------- #

    def _build_session(self) -> requests.Session:
        session = requests.Session()
        # Bounded connection pool matching the worker count (no unbounded reuse).
        adapter = requests.adapters.HTTPAdapter(pool_connections=MAX_WORKERS, pool_maxsize=MAX_WORKERS)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.headers.update({"User-Agent": self.config.user_agent})
        return session

    def _throttle(self) -> None:
        """Enforce a global minimum spacing of ``delay_s`` between requests."""
        with self._rate_lock:
            now = time.monotonic()
            wait = self._last_request_ts + self.delay_s - now
            if wait > 0:
                time.sleep(wait)
            self._last_request_ts = time.monotonic()

    def _get(self, url: str) -> requests.Response:
        self._throttle()
        return self.session.get(url, timeout=REQUEST_TIMEOUT)

    def _load_robots(self) -> RobotsPolicy:
        if self._robots is not None:
            return self._robots
        robots_url = f"{self.config.base_url}/robots.txt"
        try:
            response = self._get(robots_url)
            if response.status_code == 200:
                self._robots = RobotsPolicy.from_text(response.text, self.config.user_agent)
            else:
                self._robots = RobotsPolicy([])
        except requests.RequestException as exc:
            if self.logger:
                _log_event(self.logger, logging.WARNING, "robots_fetch_failed",
                           url=robots_url, status="", error=str(exc))
            self._robots = RobotsPolicy([])
        return self._robots

    # -- Discovery -------------------------------------------------------- #

    def discover_urls(self) -> list[str]:
        """Fetch the sitemap(s) and return product URLs (deduplicated)."""
        seen: set[str] = set()
        products: list[str] = []
        to_visit = [f"{self.config.base_url}/sitemap.xml"]
        visited_sitemaps: set[str] = set()

        while to_visit:
            sitemap_url = to_visit.pop(0)
            if sitemap_url in visited_sitemaps:
                continue
            visited_sitemaps.add(sitemap_url)
            locs, nested = self._parse_sitemap(sitemap_url)
            for nested_url in nested:
                if nested_url not in visited_sitemaps:
                    to_visit.append(nested_url)
            for loc in locs:
                if loc in seen:
                    continue
                if self.config.product_url_regex.search(loc):
                    seen.add(loc)
                    products.append(loc)
                    if len(products) >= self.max_pages:
                        return products
        return products

    def _parse_sitemap(self, sitemap_url: str) -> tuple[list[str], list[str]]:
        """Return ``(page_locs, nested_sitemap_locs)`` for one sitemap file."""
        try:
            start = time.monotonic()
            response = self._get(sitemap_url)
            latency = (time.monotonic() - start) * 1000
            if response.status_code != 200:
                if self.logger:
                    _log_event(self.logger, logging.WARNING, "sitemap_status",
                               url=sitemap_url, latency_ms=latency,
                               status=response.status_code)
                return [], []
        except requests.RequestException as exc:
            if self.logger:
                _log_event(self.logger, logging.ERROR, "sitemap_fetch_error",
                           url=sitemap_url, error=str(exc))
            return [], []

        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as exc:
            if self.logger:
                _log_event(self.logger, logging.ERROR, "sitemap_parse_error",
                           url=sitemap_url, error=str(exc))
            return [], []

        tag = root.tag.lower()
        page_locs: list[str] = []
        nested: list[str] = []
        if tag.endswith("sitemapindex"):
            for loc in root.findall(".//sm:sitemap/sm:loc", _SITEMAP_NS):
                if loc.text:
                    nested.append(loc.text.strip())
            # Fallback for sitemaps without the declared namespace.
            if not nested:
                nested = [e.text.strip() for e in root.iter() if e.tag.lower().endswith("loc") and e.text]
        else:
            for loc in root.findall(".//sm:url/sm:loc", _SITEMAP_NS):
                if loc.text:
                    page_locs.append(loc.text.strip())
            if not page_locs:
                page_locs = [e.text.strip() for e in root.iter() if e.tag.lower().endswith("loc") and e.text]
        return page_locs, nested

    # -- Fetch ------------------------------------------------------------ #

    def fetch_card(self, url: str) -> ProductCard | None:
        """GET a product page and extract a card, or ``None`` on failure."""
        if not self._load_robots().allowed(url):
            if self.logger:
                _log_event(self.logger, logging.WARNING, "robots_disallow", url=url)
            return None
        start = time.monotonic()
        try:
            response = self._get(url)
        except requests.RequestException as exc:
            latency = (time.monotonic() - start) * 1000
            if self.logger:
                _log_event(self.logger, logging.ERROR, "fetch_error",
                           url=url, latency_ms=latency, error=str(exc))
            return None
        latency = (time.monotonic() - start) * 1000
        if response.status_code != 200:
            if self.logger:
                _log_event(self.logger, logging.WARNING, "fetch_status",
                           url=url, latency_ms=latency, status=response.status_code)
            return None
        try:
            soup = BeautifulSoup(response.content, "lxml")
            card = extract_card(soup, url, self.config)
        except Exception as exc:  # noqa: BLE001 - defensive parse guard, logged.
            if self.logger:
                _log_event(self.logger, logging.ERROR, "parse_error",
                           url=url, latency_ms=latency, error=str(exc))
            return None
        if not card.is_complete():
            if self.logger:
                _log_event(self.logger, logging.WARNING, "incomplete_card",
                           url=url, latency_ms=latency, status=response.status_code,
                           error="missing sku/title/price")
            return None
        if self.logger:
            _log_event(self.logger, logging.INFO, "fetch_ok",
                       url=url, latency_ms=latency, status=response.status_code)
        return card

    # -- Persistence ------------------------------------------------------ #

    def _create_run(self, conn: sqlite3.Connection, started_at: str) -> int:
        cursor = conn.execute(
            "INSERT INTO runs (site, started_at) VALUES (?, ?)",
            (self.site, started_at),
        )
        conn.commit()
        return int(cursor.lastrowid)

    def _persist_cards(self, conn: sqlite3.Connection, run_id: int, cards: list[ProductCard]) -> None:
        captured_at = _utcnow()
        for card in cards:
            existing = conn.execute(
                "SELECT id, first_seen_run_id FROM products WHERE site = ? AND url = ?",
                (self.site, card.url),
            ).fetchone()
            first_seen = existing["first_seen_run_id"] if existing else run_id
            conn.execute(
                """
                INSERT INTO products (
                    sku, site, url, title, category, price_rub, in_stock,
                    description_short, image_main_url, image_count,
                    first_seen_run_id, last_seen_run_id, is_current
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(url) DO UPDATE SET
                    sku=excluded.sku,
                    title=excluded.title,
                    category=excluded.category,
                    price_rub=excluded.price_rub,
                    in_stock=excluded.in_stock,
                    description_short=excluded.description_short,
                    image_main_url=excluded.image_main_url,
                    image_count=excluded.image_count,
                    last_seen_run_id=excluded.last_seen_run_id,
                    is_current=1
                """,
                (
                    card.sku, self.site, card.url, card.title, card.category,
                    card.price_rub, _bool_to_int(card.in_stock),
                    card.description_short, card.image_main_url, card.image_count,
                    first_seen, run_id,
                ),
            )
            conn.execute(
                """
                INSERT INTO product_history (sku, site, run_id, price_rub, in_stock, title, captured_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (card.sku, self.site, run_id, card.price_rub,
                 _bool_to_int(card.in_stock), card.title, captured_at),
            )
        # Products not seen in this run are no longer current.
        conn.execute(
            "UPDATE products SET is_current = 0 WHERE site = ? AND last_seen_run_id != ?",
            (self.site, run_id),
        )
        conn.commit()

    def _finalize_run(self, conn: sqlite3.Connection, run_id: int, summary: RunSummary) -> None:
        conn.execute(
            """
            UPDATE runs SET finished_at = ?, urls_discovered = ?,
                urls_fetched_ok = ?, urls_failed = ? WHERE id = ?
            """,
            (summary.finished_at, summary.urls_discovered,
             summary.urls_fetched_ok, summary.urls_failed, run_id),
        )
        conn.commit()

    # -- Orchestration ---------------------------------------------------- #

    def run(self) -> RunSummary:
        started_at = _utcnow()
        summary = RunSummary(site=self.site, started_at=started_at)
        run_label = "dryrun" if self.dry_run else "pending"
        self.logger = _build_logger(self.site, run_label)
        _log_event(self.logger, logging.INFO, "run_start", status=self.site,
                   error=f"dry_run={self.dry_run}")

        try:
            urls = self.discover_urls()
        except Exception as exc:  # noqa: BLE001 - top-level guard, logged & surfaced.
            summary.error = f"discovery_failed: {exc}"
            summary.finished_at = _utcnow()
            _log_event(self.logger, logging.ERROR, "discovery_failed", error=str(exc))
            return summary

        self.last_discovered = urls
        summary.urls_discovered = len(urls)
        _log_event(self.logger, logging.INFO, "discovery_done",
                   status=len(urls))

        if self.dry_run:
            summary.finished_at = _utcnow()
            _log_event(self.logger, logging.INFO, "dry_run_complete", status=len(urls))
            return summary

        conn = init_db(self.db_path)
        try:
            run_id = self._create_run(conn, started_at)
            # Rebind logger to the concrete run id now that it exists.
            self.logger = _build_logger(self.site, run_id)

            cards: list[ProductCard] = []
            failed = 0
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = {executor.submit(self.fetch_card, url): url for url in urls}
                for future in as_completed(futures):
                    card = future.result()
                    if card is None:
                        failed += 1
                    else:
                        cards.append(card)

            summary.urls_fetched_ok = len(cards)
            summary.urls_failed = failed

            self._persist_cards(conn, run_id, cards)

            diff = self.diff_since_last(conn=conn)
            summary.added = len(diff.added)
            summary.removed = len(diff.removed)
            summary.changed = len(diff.changed)
            summary.unchanged = len(diff.unchanged)

            summary.finished_at = _utcnow()
            self._finalize_run(conn, run_id, summary)
            _log_event(self.logger, logging.INFO, "run_complete",
                       status=f"ok={len(cards)},failed={failed}")
        except Exception as exc:  # noqa: BLE001 - surface & log unexpected failures.
            summary.error = str(exc)
            summary.finished_at = _utcnow()
            _log_event(self.logger, logging.ERROR, "run_error", error=str(exc))
        finally:
            conn.close()
        return summary

    # -- Diff ------------------------------------------------------------- #

    def diff_since_last(
        self,
        *,
        conn: sqlite3.Connection | None = None,
        since_run: int | None = None,
    ) -> DiffReport:
        """Compare the two most recent runs (or ``since_run`` vs the run before)."""
        owns_conn = conn is None
        if conn is None:
            conn = init_db(self.db_path)
        try:
            run_ids = [
                row["id"]
                for row in conn.execute(
                    "SELECT id FROM runs WHERE site = ? ORDER BY id DESC",
                    (self.site,),
                ).fetchall()
            ]
            if since_run is not None:
                if since_run not in run_ids:
                    return DiffReport(site=self.site, prev_run_id=None, curr_run_id=None)
                curr_run_id = since_run
                earlier = [r for r in run_ids if r < since_run]
                prev_run_id = earlier[0] if earlier else None
            else:
                curr_run_id = run_ids[0] if run_ids else None
                prev_run_id = run_ids[1] if len(run_ids) > 1 else None

            report = DiffReport(site=self.site, prev_run_id=prev_run_id, curr_run_id=curr_run_id)
            if curr_run_id is None:
                return report

            curr = self._history_by_sku(conn, curr_run_id)
            prev = self._history_by_sku(conn, prev_run_id) if prev_run_id is not None else {}

            for sku, row in curr.items():
                if sku not in prev:
                    report.added.append({"sku": sku, "title": row["title"],
                                         "price_rub": row["price_rub"]})
                    continue
                changes = _field_changes(prev[sku], row)
                if changes:
                    report.changed.append({"sku": sku, "changes": changes})
                else:
                    report.unchanged.append(sku)
            for sku, row in prev.items():
                if sku not in curr:
                    report.removed.append({"sku": sku, "title": row["title"],
                                           "price_rub": row["price_rub"]})
            return report
        finally:
            if owns_conn:
                conn.close()

    def _history_by_sku(self, conn: sqlite3.Connection, run_id: int) -> dict[str, sqlite3.Row]:
        rows = conn.execute(
            "SELECT sku, price_rub, in_stock, title FROM product_history "
            "WHERE site = ? AND run_id = ?",
            (self.site, run_id),
        ).fetchall()
        return {row["sku"]: row for row in rows if row["sku"]}

    # -- Export ----------------------------------------------------------- #

    def export_csv(self, out_path: Path | str) -> int:
        """Write the current catalog to CSV. Returns the row count written."""
        conn = init_db(self.db_path)
        try:
            rows = conn.execute(
                """
                SELECT sku, title, category, price_rub, in_stock,
                       description_short, image_main_url, image_count, url,
                       first_seen_run_id, last_seen_run_id
                FROM products WHERE site = ? AND is_current = 1
                ORDER BY category, sku
                """,
                (self.site,),
            ).fetchall()
        finally:
            conn.close()

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        columns = [
            "sku", "title", "category", "price_rub", "in_stock",
            "description_short", "image_main_url", "image_count", "url",
            "first_seen_run_id", "last_seen_run_id",
        ]
        with open(out_path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
            for row in rows:
                writer.writerow([row[col] for col in columns])
        return len(rows)


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def _bool_to_int(value: bool | None) -> int | None:
    if value is None:
        return None
    return 1 if value else 0


def _field_changes(prev: sqlite3.Row, curr: sqlite3.Row) -> dict:
    """Field-level diff between two history rows for the same SKU."""
    changes: dict[str, dict] = {}
    for field_name in ("price_rub", "in_stock", "title"):
        old = prev[field_name]
        new = curr[field_name]
        if old != new:
            changes[field_name] = {"old": old, "new": new}
    return changes


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _print_summary(summary: RunSummary) -> None:
    print(
        f"[{summary.site}] discovered={summary.urls_discovered} "
        f"fetched_ok={summary.urls_fetched_ok} failed={summary.urls_failed} "
        f"added={summary.added} removed={summary.removed} "
        f"changed={summary.changed} unchanged={summary.unchanged}"
        + (f" error={summary.error}" if summary.error else ""),
        file=sys.stderr,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Product catalog parser.")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="sites.yaml path.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_parse = sub.add_parser("parse", help="Discover + fetch + persist + diff.")
    p_parse.add_argument("--site", required=True)
    p_parse.add_argument("--dry-run", action="store_true")
    p_parse.add_argument("--max-pages", type=int, default=5000)
    p_parse.add_argument("--delay", type=float, default=1.0)
    p_parse.add_argument("--json", action="store_true")

    p_diff = sub.add_parser("diff", help="Show diff between the last two runs.")
    p_diff.add_argument("--site", required=True)
    p_diff.add_argument("--since-run", type=int, default=None)
    p_diff.add_argument("--json", action="store_true")

    p_export = sub.add_parser("export", help="Export the current catalog to CSV.")
    p_export.add_argument("--site", required=True)
    p_export.add_argument("--out", required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "parse":
        catalog = ProductCatalogParser(
            args.site, Path(args.db), dry_run=args.dry_run,
            max_pages=args.max_pages, delay_s=args.delay, config_path=args.config,
        )
        summary = catalog.run()
        if args.dry_run:
            # In dry-run we surface the URLs discovered during the run.
            for url in catalog.last_discovered:
                print(url)
        if args.json:
            print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))
        _print_summary(summary)
        return 0

    if args.command == "diff":
        catalog = ProductCatalogParser(args.site, Path(args.db), config_path=args.config)
        diff = catalog.diff_since_last(since_run=args.since_run)
        if args.json:
            print(json.dumps(diff.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(f"[{diff.site}] prev_run={diff.prev_run_id} curr_run={diff.curr_run_id}")
            print(f"  added={len(diff.added)} removed={len(diff.removed)} "
                  f"changed={len(diff.changed)} unchanged={len(diff.unchanged)}")
            for item in diff.changed:
                print(f"  ~ {item['sku']}: {item['changes']}")
        return 0

    if args.command == "export":
        catalog = ProductCatalogParser(args.site, Path(args.db), config_path=args.config)
        count = catalog.export_csv(args.out)
        print(f"Wrote {count} rows to {args.out}", file=sys.stderr)
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
