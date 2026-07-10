#!/usr/bin/env python3
"""Image audit crawler + auditor for amadey.ru and divaninfo.ru.

Standalone, read-only CLI tool. It discovers auditable pages from a site's
sitemap, fetches each page's HTML, extracts every ``<img>`` (including
``<picture>``/``<source>`` ``srcset`` candidates and ``loading`` hints) and
runs each image through an SEO / performance / accessibility checklist. Results
are persisted to SQLite and rendered as an HTML report.

Design constraints (see task 08 spec):
  * Read-only against the live sites. Images are NEVER downloaded to disk.
    Only an HTTP HEAD (or a ranged GET fallback of the first 64 KB) is used to
    learn the size / real content-type.
  * No JS execution (no Selenium/Playwright). JS-rendered images are a known
    limitation, documented in the report and README.
  * Politeness: custom User-Agent, per-request delay, hard request-rate ceiling
    and robots.txt is respected for both sites.
  * Pages outside the audit scope (admin, cart, login, account) are skipped via
    the per-site ``audit_url_regex`` in ``config/sites.yaml``.

Usage::

    python image_audit.py run --site amadey --max-pages 10 --json
    python image_audit.py report --site amadey --last --out report.html
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import re
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Iterable, Optional, Sequence
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree as ET

import requests
import yaml
from bs4 import BeautifulSoup

LOGGER = logging.getLogger("image_audit")

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"
SITES_CONFIG = CONFIG_DIR / "sites.yaml"
RULES_CONFIG = CONFIG_DIR / "image_audit_rules.yaml"
DB_PATH = REPO_ROOT / "data" / "image_audit.db"
REPORTS_DIR = REPO_ROOT / "reports" / "image_audit"

DEFAULT_USER_AGENT = "OpenClawImageAuditBot/1.0"
RANGE_SNIFF_BYTES = 64 * 1024  # first 64 KB is enough for format sniffing


# --------------------------------------------------------------------------
# Enums / severity
# --------------------------------------------------------------------------
class Severity(str, Enum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"


class IssueType(str, Enum):
    MISSING_ALT = "MISSING_ALT"
    EMPTY_SRC = "EMPTY_SRC"
    OVERSIZE_FILE = "OVERSIZE_FILE"
    UNOPTIMIZED_FORMAT = "UNOPTIMIZED_FORMAT"
    MISSING_DIMENSIONS = "MISSING_DIMENSIONS"
    LAZY_MISSING_ABOVE_FOLD = "LAZY_MISSING_ABOVE_FOLD"
    BROKEN_URL = "BROKEN_URL"
    DUPLICATE_HASH = "DUPLICATE_HASH"
    ALT_TOO_LONG = "ALT_TOO_LONG"
    ALT_KEYWORD_STUFFED = "ALT_KEYWORD_STUFFED"


# Fallback severities used when the rules file omits a type.
DEFAULT_SEVERITY: dict[str, str] = {
    IssueType.MISSING_ALT.value: Severity.MAJOR.value,
    IssueType.EMPTY_SRC.value: Severity.CRITICAL.value,
    IssueType.OVERSIZE_FILE.value: Severity.MAJOR.value,
    IssueType.UNOPTIMIZED_FORMAT.value: Severity.MINOR.value,
    IssueType.MISSING_DIMENSIONS.value: Severity.MAJOR.value,
    IssueType.LAZY_MISSING_ABOVE_FOLD.value: Severity.MINOR.value,
    IssueType.BROKEN_URL.value: Severity.CRITICAL.value,
    IssueType.DUPLICATE_HASH.value: Severity.MINOR.value,
    IssueType.ALT_TOO_LONG.value: Severity.MINOR.value,
    IssueType.ALT_KEYWORD_STUFFED.value: Severity.MINOR.value,
}

DEFAULT_THRESHOLDS: dict[str, float] = {
    "png_max_kb": 200,
    "jpg_max_kb": 300,
    "webp_max_kb": 500,
    "avif_max_kb": 500,
    "gif_max_kb": 500,
    "alt_max_chars": 150,
    "alt_max_words": 8,
    "alt_max_phrases": 4,
    "lazy_threshold_px": 500,
    "duplicate_url_threshold": 5,
    "progressive_jpg_expected": True,
}

# DOM-order proxy for "above the fold": the first few images on a page are
# assumed to render within the first viewport. We cannot measure real pixel
# offsets without executing JS / CSS, which is out of scope.
ABOVE_FOLD_DOM_COUNT = 3


# --------------------------------------------------------------------------
# Data models
# --------------------------------------------------------------------------
@dataclass
class Issue:
    """A single audit finding for one image."""

    type: str
    severity: str
    message: str
    recommendation_url: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ImageRecord:
    """Everything we know about one ``<img>`` on one page."""

    page_url: str
    src: str
    alt: Optional[str] = None  # None => attribute absent, "" => present but empty
    alt_present: bool = False
    width: Optional[int] = None
    height: Optional[int] = None
    fmt: str = "unknown"
    file_size_kb: Optional[float] = None
    loading: Optional[str] = None
    srcset: str = ""
    above_fold: bool = False
    in_link: bool = False
    status_code: Optional[int] = None
    content_type: Optional[str] = None
    issues: list[Issue] = field(default_factory=list)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["issues"] = [i.to_dict() for i in self.issues]
        return data


@dataclass
class CrawlResult:
    site: str
    pages: list[str]
    images: list[ImageRecord]

    def to_dict(self) -> dict:
        return {
            "site": self.site,
            "pages": self.pages,
            "images": [img.to_dict() for img in self.images],
        }


@dataclass
class AuditReport:
    site: str
    started_at: str
    finished_at: str
    pages_crawled: int
    images_total: int
    issues_total: int
    severity_counts: dict[str, int]
    issue_type_counts: dict[str, int]
    images: list[ImageRecord]
    run_id: Optional[int] = None
    report_dir: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "site": self.site,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "pages_crawled": self.pages_crawled,
            "images_total": self.images_total,
            "issues_total": self.issues_total,
            "severity_counts": self.severity_counts,
            "issue_type_counts": self.issue_type_counts,
            "report_dir": self.report_dir,
            "images": [img.to_dict() for img in self.images],
        }


# --------------------------------------------------------------------------
# Config loading
# --------------------------------------------------------------------------
def load_yaml(path: Path) -> dict:
    """Load a YAML file, returning an empty dict when it is missing."""
    if not path.exists():
        LOGGER.warning("Config file %s not found; using defaults", path)
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_site_config(site: str, path: Path = SITES_CONFIG) -> dict:
    """Return the merged config for a single site key (e.g. ``amadey``)."""
    data = load_yaml(path)
    defaults = data.get("defaults", {}) or {}
    sites = data.get("sites", {}) or {}
    if site not in sites:
        raise KeyError(
            f"Unknown site '{site}'. Known: {sorted(sites) or '<none in config>'}"
        )
    merged = {**defaults, **(sites[site] or {})}
    return merged


def load_rules(path: Path = RULES_CONFIG) -> dict:
    """Return thresholds / severity / recommendations, filling in defaults."""
    data = load_yaml(path)
    thresholds = {**DEFAULT_THRESHOLDS, **(data.get("thresholds") or {})}
    severity = {**DEFAULT_SEVERITY, **(data.get("severity") or {})}
    recommendations = data.get("recommendations") or {}
    return {
        "thresholds": thresholds,
        "severity": severity,
        "recommendations": recommendations,
    }


# --------------------------------------------------------------------------
# Format helpers
# --------------------------------------------------------------------------
_EXT_TO_FORMAT = {
    "jpg": "jpg",
    "jpeg": "jpg",
    "jpe": "jpg",
    "png": "png",
    "webp": "webp",
    "avif": "avif",
    "gif": "gif",
    "svg": "svg",
    "bmp": "bmp",
    "ico": "ico",
}


def format_from_url(url: str) -> str:
    """Best-effort image format from a URL's file extension."""
    if not url:
        return "unknown"
    path = urlparse(url).path.lower()
    ext = path.rsplit(".", 1)[-1] if "." in path else ""
    return _EXT_TO_FORMAT.get(ext, "unknown")


def format_from_content_type(content_type: Optional[str]) -> str:
    """Map an HTTP Content-Type header to our format vocabulary."""
    if not content_type:
        return "unknown"
    ct = content_type.split(";", 1)[0].strip().lower()
    if ct.startswith("image/"):
        sub = ct.split("/", 1)[1]
        if sub in ("jpeg", "jpg", "pjpeg"):
            return "jpg"
        return _EXT_TO_FORMAT.get(sub, sub)
    return "unknown"


def sniff_format(chunk: bytes) -> str:
    """Sniff an image format from the first bytes using Pillow when available.

    Only the first ~64 KB should ever be passed in. Falls back to signature
    matching so the tool works even without Pillow installed.
    """
    if not chunk:
        return "unknown"
    # Signature-based fast path (no external deps).
    if chunk[:3] == b"\xff\xd8\xff":
        return "jpg"
    if chunk[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if chunk[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if chunk[:4] == b"RIFF" and chunk[8:12] == b"WEBP":
        return "webp"
    if chunk[4:12] in (b"ftypavif", b"ftypavis"):
        return "avif"
    if chunk[:5] == b"<?xml" or chunk.lstrip()[:4] == b"<svg":
        return "svg"
    try:
        from PIL import Image  # local import keeps Pillow optional

        with Image.open(io.BytesIO(chunk)) as im:
            return _EXT_TO_FORMAT.get((im.format or "").lower(), "unknown")
    except Exception:  # noqa: BLE001 - sniffing is best-effort, never fatal
        return "unknown"


def is_progressive_jpeg(chunk: bytes) -> Optional[bool]:
    """Return True/False if the JPEG progressive flag can be read, else None."""
    if not chunk or chunk[:3] != b"\xff\xd8\xff":
        return None
    # Scan JPEG markers for SOF2 (0xFFC2 = progressive DCT).
    i = 2
    n = len(chunk)
    while i + 1 < n:
        if chunk[i] != 0xFF:
            i += 1
            continue
        marker = chunk[i + 1]
        if marker == 0xC2:
            return True
        if marker in (0xC0, 0xC1, 0xC3):
            return False
        # Skip this segment using its length field when present.
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if i + 3 >= n:
            break
        seg_len = (chunk[i + 2] << 8) + chunk[i + 3]
        i += 2 + seg_len
    return None


# --------------------------------------------------------------------------
# Pure auditor
# --------------------------------------------------------------------------
def _int_or_none(value) -> Optional[int]:
    try:
        return int(str(value).strip().replace("px", ""))
    except (TypeError, ValueError):
        return None


def audit_image(
    img: ImageRecord,
    thresholds: Optional[dict] = None,
    severity: Optional[dict] = None,
    recommendations: Optional[dict] = None,
    duplicate_srcs: Optional[set[str]] = None,
) -> list[Issue]:
    """Pure function: audit one image, returning the list of issues found.

    No network access happens here; ``img`` must already carry any HEAD-derived
    fields (``file_size_kb``, ``status_code``, ``fmt``). ``duplicate_srcs`` is
    the set of image URLs known to appear more than the duplicate threshold.
    """
    thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    severity = {**DEFAULT_SEVERITY, **(severity or {})}
    recommendations = recommendations or {}
    duplicate_srcs = duplicate_srcs or set()

    def make(issue_type: IssueType, message: str) -> Issue:
        rec = recommendations.get(issue_type.value, {}) or {}
        return Issue(
            type=issue_type.value,
            severity=severity.get(issue_type.value, Severity.MINOR.value),
            message=message,
            recommendation_url=rec.get("url", ""),
        )

    issues: list[Issue] = []

    # --- EMPTY_SRC -------------------------------------------------------
    src = (img.src or "").strip()
    if src in ("", "#") or src.lower().startswith("data:,"):
        issues.append(make(IssueType.EMPTY_SRC, "Image has empty or placeholder src."))
        # Nothing else is meaningful without a real source.
        return issues

    # --- BROKEN_URL ------------------------------------------------------
    if img.status_code is not None and (
        img.status_code == 0 or img.status_code >= 400
    ):
        issues.append(
            make(
                IssueType.BROKEN_URL,
                f"Image request returned status {img.status_code}.",
            )
        )

    # --- MISSING_ALT -----------------------------------------------------
    alt = img.alt
    if not img.alt_present or alt is None or alt.strip() == "":
        # Empty alt is valid for decorative images, but a content image inside a
        # link almost always needs alt text. We flag any missing/empty alt and
        # bump severity when the image is a primary/linked asset.
        issue = make(IssueType.MISSING_ALT, "Image is missing descriptive alt text.")
        # A linked or above-the-fold (primary) image almost always needs alt
        # text; bump the severity to major in those cases.
        if img.in_link or img.above_fold:
            issue.severity = Severity.MAJOR.value
        issues.append(issue)
    else:
        alt_stripped = alt.strip()
        # --- ALT_TOO_LONG ----------------------------------------------
        if len(alt_stripped) > thresholds["alt_max_chars"]:
            issues.append(
                make(
                    IssueType.ALT_TOO_LONG,
                    f"Alt text is {len(alt_stripped)} chars "
                    f"(> {thresholds['alt_max_chars']}).",
                )
            )
        # --- ALT_KEYWORD_STUFFED ---------------------------------------
        word_count = len(alt_stripped.split())
        phrase_count = len([p for p in alt_stripped.split(",") if p.strip()])
        if (
            word_count > thresholds["alt_max_words"]
            or phrase_count > thresholds["alt_max_phrases"]
        ):
            issues.append(
                make(
                    IssueType.ALT_KEYWORD_STUFFED,
                    f"Alt text looks keyword-stuffed ({word_count} words, "
                    f"{phrase_count} comma phrases).",
                )
            )

    # --- MISSING_DIMENSIONS ---------------------------------------------
    if img.width is None or img.height is None:
        issues.append(
            make(
                IssueType.MISSING_DIMENSIONS,
                "Image has no width/height attributes (CLS risk).",
            )
        )

    # --- OVERSIZE_FILE ---------------------------------------------------
    if img.file_size_kb is not None:
        limit = _size_limit_for_format(img.fmt, thresholds)
        if img.file_size_kb > limit:
            issues.append(
                make(
                    IssueType.OVERSIZE_FILE,
                    f"{img.fmt.upper()} is {img.file_size_kb:.0f} KB "
                    f"(> {limit:.0f} KB budget).",
                )
            )

    # --- UNOPTIMIZED_FORMAT ---------------------------------------------
    if img.fmt == "png":
        issues.append(
            make(
                IssueType.UNOPTIMIZED_FORMAT,
                "PNG used for a content image; WebP/AVIF is usually smaller.",
            )
        )

    # --- LAZY_MISSING_ABOVE_FOLD ----------------------------------------
    loading = (img.loading or "").lower()
    if img.above_fold and loading == "lazy":
        issues.append(
            make(
                IssueType.LAZY_MISSING_ABOVE_FOLD,
                "Above-the-fold image uses loading=lazy, hurting LCP.",
            )
        )
    elif not img.above_fold and loading != "lazy":
        issues.append(
            make(
                IssueType.LAZY_MISSING_ABOVE_FOLD,
                "Below-the-fold image is missing loading=lazy.",
            )
        )

    # --- DUPLICATE_HASH --------------------------------------------------
    if src in duplicate_srcs:
        issues.append(
            make(
                IssueType.DUPLICATE_HASH,
                "Image URL is reused across many pages; consider a shared CDN asset.",
            )
        )

    return issues


def _size_limit_for_format(fmt: str, thresholds: dict) -> float:
    mapping = {
        "png": thresholds["png_max_kb"],
        "jpg": thresholds["jpg_max_kb"],
        "webp": thresholds["webp_max_kb"],
        "avif": thresholds["avif_max_kb"],
    }
    return float(mapping.get(fmt, thresholds.get("gif_max_kb", 500)))


# --------------------------------------------------------------------------
# HTML parsing
# --------------------------------------------------------------------------
def extract_images(html: str, page_url: str) -> list[ImageRecord]:
    """Extract every ``<img>`` (with picture/source srcset context) from HTML."""
    soup = BeautifulSoup(html, "lxml")
    records: list[ImageRecord] = []
    for index, tag in enumerate(soup.find_all("img")):
        attrs = tag.attrs
        raw_src = attrs.get("src") or attrs.get("data-src") or ""
        srcset = attrs.get("srcset", "")

        # Pull sibling <source> srcsets when the img lives inside a <picture>.
        picture = tag.find_parent("picture")
        source_srcsets: list[str] = []
        if picture is not None:
            for source in picture.find_all("source"):
                if source.get("srcset"):
                    source_srcsets.append(source["srcset"])
        combined_srcset = ", ".join([s for s in [srcset, *source_srcsets] if s])

        # Resolve the effective src (fall back to first srcset candidate).
        effective = (raw_src or "").strip()
        if not effective and combined_srcset:
            effective = _first_srcset_url(combined_srcset)
        # Preserve placeholder/empty sentinels so EMPTY_SRC can be detected;
        # only absolute-resolve real references.
        if effective in ("", "#") or effective.lower().startswith("data:,"):
            resolved = effective
        else:
            resolved = urljoin(page_url, effective)

        alt_present = "alt" in attrs
        alt_value = attrs.get("alt") if alt_present else None

        record = ImageRecord(
            page_url=page_url,
            src=resolved,
            alt=alt_value,
            alt_present=alt_present,
            width=_int_or_none(attrs.get("width")),
            height=_int_or_none(attrs.get("height")),
            fmt=format_from_url(resolved),
            loading=(attrs.get("loading") or None),
            srcset=combined_srcset,
            above_fold=index < ABOVE_FOLD_DOM_COUNT,
            in_link=tag.find_parent("a") is not None,
        )
        records.append(record)
    return records


def _first_srcset_url(srcset: str) -> str:
    first = srcset.split(",", 1)[0].strip()
    return first.split()[0] if first else ""


# --------------------------------------------------------------------------
# Rate limiter
# --------------------------------------------------------------------------
class RateLimiter:
    """Thread-safe minimum-interval throttle shared by crawler workers."""

    def __init__(self, delay_s: float, max_rps: float) -> None:
        interval_from_rps = 1.0 / max_rps if max_rps and max_rps > 0 else 0.0
        self._min_interval = max(float(delay_s), interval_from_rps)
        self._lock = threading.Lock()
        self._next_time = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            if now < self._next_time:
                time.sleep(self._next_time - now)
                now = time.monotonic()
            self._next_time = now + self._min_interval


# --------------------------------------------------------------------------
# SQLite persistence
# --------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    pages_crawled INTEGER DEFAULT 0,
    images_total INTEGER DEFAULT 0,
    issues_total INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    page_url TEXT NOT NULL,
    src TEXT,
    alt TEXT,
    width INTEGER,
    height INTEGER,
    format TEXT,
    file_size_kb REAL,
    loading TEXT
);

CREATE TABLE IF NOT EXISTS issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT,
    recommendation_url TEXT
);

CREATE INDEX IF NOT EXISTS idx_issues_run_type ON issues(run_id, type);
CREATE INDEX IF NOT EXISTS idx_issues_run_severity ON issues(run_id, severity);
CREATE INDEX IF NOT EXISTS idx_images_run ON images(run_id);
"""


def connect_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def persist_report(conn: sqlite3.Connection, report: AuditReport) -> int:
    """Write a run + its images + issues, returning the new run id."""
    init_db(conn)
    cur = conn.execute(
        "INSERT INTO runs (site, started_at, finished_at, pages_crawled, "
        "images_total, issues_total) VALUES (?, ?, ?, ?, ?, ?)",
        (
            report.site,
            report.started_at,
            report.finished_at,
            report.pages_crawled,
            report.images_total,
            report.issues_total,
        ),
    )
    run_id = int(cur.lastrowid)
    for img in report.images:
        icur = conn.execute(
            "INSERT INTO images (run_id, page_url, src, alt, width, height, "
            "format, file_size_kb, loading) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                img.page_url,
                img.src,
                img.alt,
                img.width,
                img.height,
                img.fmt,
                img.file_size_kb,
                img.loading,
            ),
        )
        image_id = int(icur.lastrowid)
        for issue in img.issues:
            conn.execute(
                "INSERT INTO issues (run_id, image_id, type, severity, message, "
                "recommendation_url) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    image_id,
                    issue.type,
                    issue.severity,
                    issue.message,
                    issue.recommendation_url,
                ),
            )
    conn.commit()
    report.run_id = run_id
    return run_id


# --------------------------------------------------------------------------
# The bot
# --------------------------------------------------------------------------
class ImageAuditBot:
    """Crawl a configured site, audit its images and produce a report."""

    def __init__(
        self,
        site: str,
        *,
        max_pages: int = 1000,
        delay_s: float = 1.0,
        download_thumbnail: bool = False,
        sites_config: Path = SITES_CONFIG,
        rules_config: Path = RULES_CONFIG,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.site = site
        self.config = load_site_config(site, sites_config)
        self.rules = load_rules(rules_config)
        self.thresholds = self.rules["thresholds"]
        self.severity = self.rules["severity"]
        self.recommendations = self.rules["recommendations"]

        self.base_url = self.config.get("base_url", f"https://{site}")
        self.sitemap_url = self.config.get(
            "sitemap_url", urljoin(self.base_url + "/", "sitemap.xml")
        )
        self.audit_url_regex = re.compile(self.config.get("audit_url_regex", ".*"))
        self.user_agent = self.config.get("user_agent", DEFAULT_USER_AGENT)
        self.max_pages = int(max_pages)
        self.delay_s = float(delay_s)
        self.max_rps = float(self.config.get("max_rps", 3.0))
        self.download_thumbnail = download_thumbnail

        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": self.user_agent})
        self.rate_limiter = RateLimiter(self.delay_s, self.max_rps)
        self._robots = self._load_robots()

    # -- robots ---------------------------------------------------------
    def _load_robots(self) -> Optional[RobotFileParser]:
        robots_url = urljoin(self.base_url + "/", "robots.txt")
        parser = RobotFileParser()
        try:
            self.rate_limiter.wait()
            resp = self.session.get(robots_url, timeout=10)
        except requests.RequestException as exc:
            LOGGER.warning("Could not fetch robots.txt (%s): %s", robots_url, exc)
            return None
        if resp.status_code >= 400:
            LOGGER.info("robots.txt returned %s; assuming allow-all", resp.status_code)
            return None
        parser.parse(resp.text.splitlines())
        return parser

    def _allowed(self, url: str) -> bool:
        if self._robots is None:
            return True
        return self._robots.can_fetch(self.user_agent, url)

    # -- discovery ------------------------------------------------------
    def discover_pages(self) -> list[str]:
        """Return in-scope page URLs discovered from the sitemap."""
        urls = self._fetch_sitemap_urls(self.sitemap_url, depth=0)
        scoped: list[str] = []
        seen: set[str] = set()
        for url in urls:
            if url in seen:
                continue
            if not self.audit_url_regex.search(url):
                continue
            if not self._allowed(url):
                LOGGER.info("Skipping (robots): %s", url)
                continue
            seen.add(url)
            scoped.append(url)
            if len(scoped) >= self.max_pages:
                break
        return scoped

    def _fetch_sitemap_urls(self, sitemap_url: str, depth: int) -> list[str]:
        if depth > 3:
            return []
        try:
            self.rate_limiter.wait()
            resp = self.session.get(sitemap_url, timeout=15)
        except requests.RequestException as exc:
            LOGGER.warning("Sitemap fetch failed (%s): %s", sitemap_url, exc)
            return []
        if resp.status_code >= 400:
            LOGGER.warning("Sitemap %s returned %s", sitemap_url, resp.status_code)
            return []
        return self._parse_sitemap(resp.content, depth)

    def _parse_sitemap(self, content: bytes, depth: int) -> list[str]:
        try:
            root = ET.fromstring(content)
        except ET.ParseError as exc:
            LOGGER.warning("Could not parse sitemap XML: %s", exc)
            return []
        tag = root.tag.lower()
        locs = [
            (el.text or "").strip()
            for el in root.iter()
            if el.tag.lower().endswith("loc") and el.text
        ]
        if tag.endswith("sitemapindex"):
            nested: list[str] = []
            for loc in locs:
                nested.extend(self._fetch_sitemap_urls(loc, depth + 1))
            return nested
        return locs

    # -- HEAD image -----------------------------------------------------
    def head_image(self, url: str, timeout: int = 5) -> tuple[int, str, int]:
        """Return ``(status_code, content_type, content_length)`` for an image.

        Uses ``requests.head`` first and falls back to a ranged GET (first
        64 KB) when the server rejects HEAD or omits a usable Content-Length.
        Never downloads the full image. On network failure returns status 0.
        """
        try:
            self.rate_limiter.wait()
            resp = self.session.head(url, timeout=timeout, allow_redirects=True)
        except requests.RequestException as exc:
            LOGGER.info("HEAD failed for %s: %s; trying ranged GET", url, exc)
            return self._ranged_get(url, timeout)

        content_type = resp.headers.get("Content-Type", "")
        length = _parse_int_header(resp.headers.get("Content-Length"))
        if resp.status_code >= 400 or resp.status_code == 405 or length is None:
            # HEAD unsupported or unhelpful -> ranged GET fallback.
            status, ct, clen = self._ranged_get(url, timeout)
            if status and status < 400:
                return status, ct or content_type, clen
            return (resp.status_code, content_type, length or 0)
        return (resp.status_code, content_type, length)

    def _ranged_get(self, url: str, timeout: int) -> tuple[int, str, int]:
        headers = {"Range": f"bytes=0-{RANGE_SNIFF_BYTES - 1}"}
        try:
            self.rate_limiter.wait()
            resp = self.session.get(
                url, headers=headers, timeout=timeout, stream=True
            )
        except requests.RequestException as exc:
            LOGGER.info("Ranged GET failed for %s: %s", url, exc)
            return (0, "", 0)
        content_type = resp.headers.get("Content-Type", "")
        # Prefer Content-Range total, then Content-Length, then sniff chunk size.
        length = _length_from_range(resp.headers.get("Content-Range"))
        if length is None:
            length = _parse_int_header(resp.headers.get("Content-Length"))
        chunk = b""
        if length is None or self.download_thumbnail:
            chunk = resp.raw.read(RANGE_SNIFF_BYTES) if resp.raw else b""
            if length is None:
                length = len(chunk)
        resp.close()
        return (resp.status_code, content_type, length or 0)

    # -- crawl ----------------------------------------------------------
    def crawl_pages(self, pages: Optional[Sequence[str]] = None) -> CrawlResult:
        """Fetch pages in parallel and collect every image record."""
        if pages is None:
            pages = self.discover_pages()
        images: list[ImageRecord] = []
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(self._fetch_page_images, url): url for url in pages}
            for fut in as_completed(futures):
                url = futures[fut]
                try:
                    images.extend(fut.result())
                except Exception as exc:  # noqa: BLE001 - isolate per-page failures
                    LOGGER.warning("Failed to process %s: %s", url, exc)
        return CrawlResult(site=self.site, pages=list(pages), images=images)

    def _fetch_page_images(self, url: str) -> list[ImageRecord]:
        try:
            self.rate_limiter.wait()
            resp = self.session.get(url, timeout=15)
        except requests.RequestException as exc:
            LOGGER.warning("Page fetch failed (%s): %s", url, exc)
            return []
        if resp.status_code >= 400:
            LOGGER.info("Page %s returned %s", url, resp.status_code)
            return []
        return extract_images(resp.text, url)

    # -- head enrichment ------------------------------------------------
    def enrich_with_head(self, images: Sequence[ImageRecord]) -> None:
        """Populate size/status/format for each unique src via HEAD (cached)."""
        cache: dict[str, tuple[int, str, int]] = {}
        unique = [i for i in images if (i.src or "").strip() not in ("", "#")]
        srcs = list({i.src for i in unique})
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(self.head_image, src): src for src in srcs}
            for fut in as_completed(futures):
                src = futures[fut]
                try:
                    cache[src] = fut.result()
                except Exception as exc:  # noqa: BLE001
                    LOGGER.warning("HEAD errored for %s: %s", src, exc)
                    cache[src] = (0, "", 0)
        for img in unique:
            status, ct, length = cache.get(img.src, (None, "", 0))
            img.status_code = status
            img.content_type = ct or None
            if length:
                img.file_size_kb = round(length / 1024.0, 1)
            ct_fmt = format_from_content_type(ct)
            if img.fmt in ("unknown", "") and ct_fmt != "unknown":
                img.fmt = ct_fmt

    # -- audit ----------------------------------------------------------
    def audit_image(
        self, img: ImageRecord, duplicate_srcs: Optional[set[str]] = None
    ) -> list[Issue]:
        """Audit a single image using this bot's configured rules."""
        return audit_image(
            img,
            thresholds=self.thresholds,
            severity=self.severity,
            recommendations=self.recommendations,
            duplicate_srcs=duplicate_srcs,
        )

    def _compute_duplicate_srcs(self, images: Sequence[ImageRecord]) -> set[str]:
        threshold = int(self.thresholds.get("duplicate_url_threshold", 5))
        counts: dict[str, set[str]] = {}
        for img in images:
            src = (img.src or "").strip()
            if not src or src == "#":
                continue
            counts.setdefault(src, set()).add(img.page_url)
        return {src for src, pages in counts.items() if len(pages) > threshold}

    # -- orchestration --------------------------------------------------
    def run(
        self,
        *,
        pages: Optional[Sequence[str]] = None,
        persist: bool = True,
        write_report: bool = True,
        db_path: Path = DB_PATH,
    ) -> AuditReport:
        """Full pipeline: crawl -> HEAD enrich -> audit -> persist -> report."""
        started_at = datetime.now().isoformat(timespec="seconds")
        crawl = self.crawl_pages(pages)
        self.enrich_with_head(crawl.images)

        duplicate_srcs = self._compute_duplicate_srcs(crawl.images)
        for img in crawl.images:
            img.issues = self.audit_image(img, duplicate_srcs=duplicate_srcs)

        finished_at = datetime.now().isoformat(timespec="seconds")
        report = self._build_report(crawl, started_at, finished_at)

        if persist:
            conn = connect_db(db_path)
            try:
                persist_report(conn, report)
            finally:
                conn.close()

        if write_report:
            report.report_dir = self._write_html_report(report)

        return report

    def _build_report(
        self, crawl: CrawlResult, started_at: str, finished_at: str
    ) -> AuditReport:
        severity_counts = {s.value: 0 for s in Severity}
        issue_type_counts = {t.value: 0 for t in IssueType}
        issues_total = 0
        for img in crawl.images:
            for issue in img.issues:
                issues_total += 1
                severity_counts[issue.severity] = (
                    severity_counts.get(issue.severity, 0) + 1
                )
                issue_type_counts[issue.type] = (
                    issue_type_counts.get(issue.type, 0) + 1
                )
        return AuditReport(
            site=self.site,
            started_at=started_at,
            finished_at=finished_at,
            pages_crawled=len(crawl.pages),
            images_total=len(crawl.images),
            issues_total=issues_total,
            severity_counts=severity_counts,
            issue_type_counts=issue_type_counts,
            images=crawl.images,
        )

    def _write_html_report(self, report: AuditReport) -> str:
        # Imported lazily to avoid a hard dependency for pure audit use.
        from image_audit_report import write_report as _write

        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        out_dir = REPORTS_DIR / f"{self.site}_{stamp}"
        _write(report, out_dir, rules=self.rules)
        return str(out_dir)


# --------------------------------------------------------------------------
# Header helpers
# --------------------------------------------------------------------------
def _parse_int_header(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _length_from_range(content_range: Optional[str]) -> Optional[int]:
    # Format: "bytes 0-65535/1234567"
    if not content_range or "/" not in content_range:
        return None
    total = content_range.rsplit("/", 1)[-1].strip()
    if total in ("", "*"):
        return None
    try:
        return int(total)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# DB read helpers (for report --last)
# --------------------------------------------------------------------------
def load_last_report(site: str, db_path: Path = DB_PATH) -> Optional[AuditReport]:
    """Reconstruct the most recent AuditReport for a site from SQLite."""
    conn = connect_db(db_path)
    try:
        init_db(conn)
        row = conn.execute(
            "SELECT * FROM runs WHERE site = ? ORDER BY id DESC LIMIT 1", (site,)
        ).fetchone()
        if row is None:
            return None
        run_id = row["id"]
        images: list[ImageRecord] = []
        img_rows = conn.execute(
            "SELECT * FROM images WHERE run_id = ? ORDER BY id", (run_id,)
        ).fetchall()
        issue_rows = conn.execute(
            "SELECT * FROM issues WHERE run_id = ? ORDER BY id", (run_id,)
        ).fetchall()
        issues_by_image: dict[int, list[Issue]] = {}
        for ir in issue_rows:
            issues_by_image.setdefault(ir["image_id"], []).append(
                Issue(
                    type=ir["type"],
                    severity=ir["severity"],
                    message=ir["message"] or "",
                    recommendation_url=ir["recommendation_url"] or "",
                )
            )
        severity_counts = {s.value: 0 for s in Severity}
        issue_type_counts = {t.value: 0 for t in IssueType}
        for imr in img_rows:
            rec = ImageRecord(
                page_url=imr["page_url"],
                src=imr["src"],
                alt=imr["alt"],
                alt_present=imr["alt"] is not None,
                width=imr["width"],
                height=imr["height"],
                fmt=imr["format"] or "unknown",
                file_size_kb=imr["file_size_kb"],
                loading=imr["loading"],
            )
            rec.issues = issues_by_image.get(imr["id"], [])
            for issue in rec.issues:
                severity_counts[issue.severity] = (
                    severity_counts.get(issue.severity, 0) + 1
                )
                issue_type_counts[issue.type] = (
                    issue_type_counts.get(issue.type, 0) + 1
                )
            images.append(rec)
        return AuditReport(
            site=site,
            run_id=run_id,
            started_at=row["started_at"],
            finished_at=row["finished_at"] or "",
            pages_crawled=row["pages_crawled"],
            images_total=row["images_total"],
            issues_total=row["issues_total"],
            severity_counts=severity_counts,
            issue_type_counts=issue_type_counts,
            images=images,
        )
    finally:
        conn.close()


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _cmd_run(args: argparse.Namespace) -> int:
    bot = ImageAuditBot(
        args.site,
        max_pages=args.max_pages,
        delay_s=args.delay,
        download_thumbnail=args.download_thumbnail,
    )
    report = bot.run(
        persist=not args.no_db,
        write_report=not args.no_report,
        db_path=DB_PATH,
    )
    if args.json:
        json.dump(report.to_dict(), sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print(
            f"Site {report.site}: {report.pages_crawled} pages, "
            f"{report.images_total} images, {report.issues_total} issues "
            f"(critical={report.severity_counts.get('critical', 0)}, "
            f"major={report.severity_counts.get('major', 0)}, "
            f"minor={report.severity_counts.get('minor', 0)})"
        )
        if report.report_dir:
            print(f"Report: {report.report_dir}/index.html")
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    from image_audit_report import write_report as _write

    report = load_last_report(args.site, db_path=DB_PATH)
    if report is None:
        print(f"No stored runs for site '{args.site}'.", file=sys.stderr)
        return 1
    out_path = Path(args.out)
    out_dir = out_path if out_path.suffix == "" else out_path.parent
    rules = load_rules()
    index_path = _write(report, out_dir, rules=rules, index_name=out_path.name
                        if out_path.suffix == ".html" else "index.html")
    print(f"Report written to {index_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit images on amadey.ru / divaninfo.ru for SEO / perf / a11y."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Crawl a site and audit its images.")
    run_p.add_argument("--site", required=True, help="Site key, e.g. amadey|divaninfo")
    run_p.add_argument("--max-pages", type=int, default=1000)
    run_p.add_argument("--delay", type=float, default=1.0, help="Per-request delay (s)")
    run_p.add_argument("--download-thumbnail", action="store_true")
    run_p.add_argument("--json", action="store_true", help="Emit JSON to stdout")
    run_p.add_argument("--no-db", action="store_true", help="Skip SQLite persistence")
    run_p.add_argument("--no-report", action="store_true", help="Skip HTML report")
    run_p.set_defaults(func=_cmd_run)

    rep_p = sub.add_parser("report", help="Regenerate HTML from the last stored run.")
    rep_p.add_argument("--site", required=True)
    rep_p.add_argument("--last", action="store_true", help="Use the most recent run")
    rep_p.add_argument("--out", default="report.html", help="Output file or directory")
    rep_p.set_defaults(func=_cmd_report)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
