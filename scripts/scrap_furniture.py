#!/usr/bin/env python3
"""Furniture marketplace search scraping (Avito HTML + Wildberries JSON).

Used as a base layer for ``competitor_monitor.py``. Site markup and internal
APIs change over time; callers should tolerate empty results and log warnings.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote_plus, urljoin

import requests
from bs4 import BeautifulSoup

REQUEST_TIMEOUT_SECONDS = 25
REQUEST_DELAY_SECONDS = 1.2

# Wildberries internal search endpoint (see public front-end network traffic).
WB_SEARCH_URL = (
    "https://search.wb.ru/exactmatch/ru/common/v5/search"
    "?appType=1&curr=rub&dest=-1257786&lang=ru&page={page}"
    "&query={query}&resultset=catalog&sort=popular&spp=30"
)


@dataclass(frozen=True)
class FurnitureListing:
    """Normalized search result row for Avito or Wildberries."""

    source: str  # "avito" | "wb"
    query: str
    title: str
    price: float | None
    url: str
    sold: bool = False  # True when listing card clearly indicates sold (Avito).


def _default_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    }


def parse_price_ru(raw: str | None) -> float | None:
    """Parse a Russian-formatted price string into rubles (float)."""
    if not raw:
        return None
    cleaned = (
        str(raw)
        .replace("\xa0", "")
        .replace(" ", "")
        .replace(",", ".")
        .replace("₽", "")
        .replace("руб.", "")
        .replace("руб", "")
        .strip()
    )
    matches = re.findall(r"\d+(?:\.\d+)?", cleaned)
    if not matches:
        return None
    try:
        return float(Decimal(matches[-1]))
    except (InvalidOperation, ValueError):
        return None


def _wb_product_price_rub(product: dict[str, Any]) -> float | None:
    """Extract rub price from WB search product object (keys vary by API version)."""
    for key in ("salePriceU", "salePrice", "priceU", "price"):
        val = product.get(key)
        if val is None:
            continue
        try:
            num = float(val)
        except (TypeError, ValueError):
            continue
        # WB historically encodes RUB * 100 as int.
        if num > 10_000:
            num /= 100.0
        return num
    sizes = product.get("sizes")
    if isinstance(sizes, list) and sizes:
        price = sizes[0].get("price") if isinstance(sizes[0], dict) else None
        if isinstance(price, dict):
            return parse_price_ru(str(price.get("product", price.get("total", ""))))
    return None


def parse_wb_search_payload(data: dict[str, Any], query: str) -> list[FurnitureListing]:
    """Turn WB search JSON into ``FurnitureListing`` rows."""
    products: list[Any] = []
    if isinstance(data.get("data"), dict) and isinstance(data["data"].get("products"), list):
        products = data["data"]["products"]
    elif isinstance(data.get("products"), list):
        products = data["products"]

    out: list[FurnitureListing] = []
    for p in products:
        if not isinstance(p, dict):
            continue
        nm = p.get("id")
        if nm is None:
            continue
        title = str(p.get("name", "")).strip() or f"nm{nm}"
        price = _wb_product_price_rub(p)
        url = f"https://www.wildberries.ru/catalog/{nm}/detail.aspx"
        out.append(FurnitureListing(source="wb", query=query, title=title, price=price, url=url))
    return out


def parse_avito_search_html(html: str, query: str) -> list[FurnitureListing]:
    """Parse Avito search HTML (``data-marker="item"`` cards)."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[FurnitureListing] = []
    for card in soup.select('[data-marker="item"]'):
        sold = bool(
            re.search(r"продано", card.get_text(" ", strip=True), re.IGNORECASE)
            or card.select_one('[data-marker="item-sold"]')
        )
        link = card.select_one('a[data-marker="item-title"]') or card.select_one(
            "a[href*='/item/']"
        )
        if not link or not link.get("href"):
            continue
        href = str(link["href"]).strip()
        if not href.startswith("http"):
            href = urljoin("https://www.avito.ru", href)
        title = link.get_text(strip=True) or href
        price_el = card.select_one('[data-marker="item-price"]') or card.select_one(
            '[itemprop="price"]'
        )
        raw_price = price_el.get_text(" ", strip=True) if price_el else None
        if not raw_price and price_el and price_el.get("content"):
            raw_price = str(price_el["content"])
        price = parse_price_ru(raw_price)
        out.append(
            FurnitureListing(
                source="avito",
                query=query,
                title=title,
                price=price,
                url=href.split("?")[0],
                sold=sold,
            )
        )
    return out


class FurnitureScrapeSession:
    """Rate-limited HTTP session for furniture marketplace searches."""

    def __init__(self, delay_seconds: float = REQUEST_DELAY_SECONDS) -> None:
        self._delay = delay_seconds
        self._last_at = 0.0
        self.session = requests.Session()
        self.session.headers.update(_default_headers())

    def _sleep_ratelimit(self) -> None:
        elapsed = time.monotonic() - self._last_at
        if elapsed < self._delay:
            time.sleep(self._delay - elapsed)

    def get_text(self, url: str) -> str:
        self._sleep_ratelimit()
        r = self.session.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
        self._last_at = time.monotonic()
        r.raise_for_status()
        return r.text

    def get_json(self, url: str) -> Any:
        self._sleep_ratelimit()
        r = self.session.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
        self._last_at = time.monotonic()
        r.raise_for_status()
        ctype = r.headers.get("Content-Type", "")
        if "json" not in ctype.lower() and r.text.lstrip().startswith("<"):
            raise ValueError("Expected JSON from WB search, got HTML (blocked or error page).")
        return r.json()


def build_avito_search_url(query: str, region_path: str = "all") -> str:
    """Build Avito search URL (``region_path`` e.g. ``all``, ``moskva``, ``sankt-peterburg``)."""
    q = quote_plus(query)
    region_path = region_path.strip("/") or "all"
    return f"https://www.avito.ru/{region_path}?q={q}"


def scrape_avito(session: FurnitureScrapeSession, query: str, region_path: str = "all") -> list[FurnitureListing]:
    """Fetch and parse one Avito search results page."""
    url = build_avito_search_url(query, region_path=region_path)
    html = session.get_text(url)
    return parse_avito_search_html(html, query)


def scrape_wb(session: FurnitureScrapeSession, query: str, max_pages: int = 1) -> list[FurnitureListing]:
    """Fetch WB search JSON (one or more pages) and parse products."""
    all_rows: list[FurnitureListing] = []
    for page in range(1, max(1, max_pages) + 1):
        url = WB_SEARCH_URL.format(page=page, query=quote_plus(query))
        data = session.get_json(url)
        if not isinstance(data, dict):
            break
        chunk = parse_wb_search_payload(data, query)
        if not chunk:
            break
        all_rows.extend(chunk)
    return all_rows
