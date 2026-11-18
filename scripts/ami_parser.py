#!/usr/bin/env python3
"""AMI.by price intelligence tracker for furniture categories."""

from __future__ import annotations

import argparse
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Iterable
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://ami.by"
CATEGORY_URLS = {
    "Диваны": f"{BASE_URL}/catalog/divani.html",
    "Кресла": f"{BASE_URL}/catalog/chairs.html",
    "Стулья": f"{BASE_URL}/catalog/chairs.html",
    "Столы": f"{BASE_URL}/catalog/tablebooks.html",
    "Кровати": f"{BASE_URL}/catalog/allbed.html",
}
REQUEST_TIMEOUT_SECONDS = 20
REQUEST_DELAY_SECONDS = 2


@dataclass(frozen=True)
class Product:
    name: str
    category: str
    price: float | None
    url: str
    availability: str


class AmiTracker:
    def __init__(self, db_path: str = "ami_prices.db") -> None:
        self.db_path = db_path
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                )
            }
        )
        self._last_request_at = 0.0
        self._ensure_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ami_products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    price REAL,
                    url TEXT NOT NULL UNIQUE,
                    availability TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ami_price_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_id INTEGER NOT NULL,
                    price REAL,
                    date TEXT NOT NULL,
                    FOREIGN KEY(product_id) REFERENCES ami_products(id)
                )
                """
            )

    def _rate_limited_get(self, url: str) -> requests.Response:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < REQUEST_DELAY_SECONDS:
            time.sleep(REQUEST_DELAY_SECONDS - elapsed)
        response = self._session.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
        self._last_request_at = time.monotonic()
        response.raise_for_status()
        return response

    @staticmethod
    def _parse_price(raw: str) -> float | None:
        if not raw:
            return None
        cleaned = (
            raw.replace("\xa0", "")
            .replace(" ", "")
            .replace(",", ".")
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

    @staticmethod
    def _parse_availability(card: BeautifulSoup) -> str:
        text = card.get_text(" ", strip=True).lower()
        if "нет в наличии" in text:
            return "Нет в наличии"
        if "под заказ" in text:
            return "Под заказ"
        if "в наличии" in text:
            return "В наличии"
        return "Не указано"

    @staticmethod
    def _extract_name(card: BeautifulSoup) -> str:
        selectors = [
            '[itemprop="name"]',
            ".itemtitle a",
            ".product-card__title",
            ".b-product-item__name",
            "h2",
            "h3",
            "a[title]",
        ]
        for selector in selectors:
            node = card.select_one(selector)
            if node:
                text = node.get_text(" ", strip=True)
                if text:
                    return text
                title = node.get("title")
                if title:
                    return title.strip()
        link = card.find("a", href=True)
        if link:
            return link.get_text(" ", strip=True) or "Без названия"
        return "Без названия"

    @staticmethod
    def _extract_price(card: BeautifulSoup) -> float | None:
        banner = card.select_one(".itempricebanner")
        if banner:
            price_root = banner.select_one(".Pricy") or banner
            groups = []
            for node in price_root.select('span[class^="digitsGroup"]'):
                digits = re.sub(r"\D", "", node.get_text(" ", strip=True))
                if digits:
                    groups.append(digits)
            if groups:
                if len(groups) >= 2 and len(groups[-1]) == 2:
                    major = "".join(groups[:-1]) or "0"
                    minor = groups[-1]
                    return float(f"{major}.{minor}")
                return float("".join(groups))

        selectors = [
            '[itemprop="price"]',
            ".product-card__price",
            ".price",
            ".b-product-item__price",
        ]
        for selector in selectors:
            node = card.select_one(selector)
            if node:
                price = AmiTracker._parse_price(node.get_text(" ", strip=True))
                if price is not None:
                    return price
        full_text = card.get_text(" ", strip=True)
        return AmiTracker._parse_price(full_text)

    @staticmethod
    def _extract_url(card: BeautifulSoup) -> str | None:
        link_selectors = [
            '.itemtitle a[href*="/product/"]',
            "a.product-card__link",
            ".b-product-item__name a",
            'a[href*="/product/"]',
            "a[href*='/catalog/']",
            "a[href]",
        ]
        for selector in link_selectors:
            node = card.select_one(selector)
            if node and node.get("href"):
                return node["href"]
        return None

    @staticmethod
    def _category_match(category_name: str, product_name: str) -> bool:
        lowered = product_name.lower()
        if category_name == "Стулья":
            return "стул" in lowered or "табурет" in lowered
        if category_name == "Кресла":
            return "кресл" in lowered
        return True

    def _get_category_pages(self, category_url: str) -> list[str]:
        response = self._rate_limited_get(category_url)
        soup = BeautifulSoup(response.text, "html.parser")
        pages = {category_url}
        # Use category content area only to avoid crawling full site navigation.
        for anchor in soup.select(
            '#content a[href^="/catalog/"], #content a[href*="ami.by/catalog/"]'
        ):
            href = anchor.get("href")
            if not href:
                continue
            full_url = urljoin(BASE_URL, href)
            if full_url.endswith("catalog.html"):
                continue
            pages.add(full_url)
        return sorted(pages)[:25]

    def parse_category(self, category_name: str) -> list[Product]:
        category_url = CATEGORY_URLS.get(category_name)
        if not category_url:
            raise ValueError(f"Unsupported category: {category_name}")
        products: list[Product] = []
        seen_urls: set[str] = set()
        pages = self._get_category_pages(category_url)

        for page_url in pages:
            try:
                response = self._rate_limited_get(page_url)
            except requests.RequestException:
                continue
            soup = BeautifulSoup(response.text, "html.parser")
            cards = soup.select(
                ".itemsgrid .item, .item, .product-card, .b-product-item, .catalog-item, [data-product-id]"
            )
            for card in cards:
                relative_url = self._extract_url(card)
                if not relative_url:
                    continue
                full_url = urljoin(BASE_URL, relative_url)
                if "/product/" not in full_url:
                    continue
                if full_url in seen_urls:
                    continue
                seen_urls.add(full_url)

                product = Product(
                    name=self._extract_name(card),
                    category=category_name,
                    price=self._extract_price(card),
                    url=full_url,
                    availability=self._parse_availability(card),
                )
                if not self._category_match(category_name, product.name):
                    continue
                products.append(product)
        return products

    def get_all_products(self) -> list[Product]:
        all_products: list[Product] = []
        for category in CATEGORY_URLS:
            try:
                all_products.extend(self.parse_category(category))
            except requests.RequestException as error:
                print(f"[WARN] Category '{category}' request failed: {error}")
        return all_products

    def get_price_change(self, old_price: float | None, new_price: float | None) -> str:
        if old_price is None and new_price is None:
            return "n/a"
        if old_price is None and new_price is not None:
            return "new"
        if old_price is not None and new_price is None:
            return "unknown"
        if old_price == new_price:
            return "0.00%"
        if old_price == 0:
            return "inf"
        pct = ((new_price - old_price) / old_price) * 100
        arrow = "↑" if pct > 0 else "↓"
        return f"{arrow}{abs(pct):.2f}%"

    def _load_existing(self, conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
        rows = conn.execute("SELECT * FROM ami_products").fetchall()
        return {row["url"]: row for row in rows}

    def _insert_price_history(
        self, conn: sqlite3.Connection, product_id: int, price: float | None
    ) -> None:
        conn.execute(
            """
            INSERT INTO ami_price_history (product_id, price, date)
            VALUES (?, ?, ?)
            """,
            (product_id, price, date.today().isoformat()),
        )

    def check_updates(self, products: Iterable[Product]) -> dict[str, list[dict[str, str]]]:
        updates: dict[str, list[dict[str, str]]] = {
            "new_items": [],
            "price_changes": [],
        }
        with self._connect() as conn:
            existing = self._load_existing(conn)
            for product in products:
                prev = existing.get(product.url)
                if prev is None:
                    cur = conn.execute(
                        """
                        INSERT INTO ami_products (name, category, price, url, availability)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            product.name,
                            product.category,
                            product.price,
                            product.url,
                            product.availability,
                        ),
                    )
                    product_id = int(cur.lastrowid)
                    self._insert_price_history(conn, product_id, product.price)
                    updates["new_items"].append(
                        {
                            "name": product.name,
                            "category": product.category,
                            "price": str(product.price),
                            "url": product.url,
                        }
                    )
                    continue

                old_price = prev["price"]
                if (
                    old_price is not None
                    and product.price is not None
                    and float(old_price) != float(product.price)
                ) or (old_price is None and product.price is not None):
                    self._insert_price_history(conn, int(prev["id"]), product.price)
                    updates["price_changes"].append(
                        {
                            "name": product.name,
                            "category": product.category,
                            "old_price": str(old_price),
                            "new_price": str(product.price),
                            "change": self.get_price_change(
                                float(old_price) if old_price is not None else None,
                                product.price,
                            ),
                            "url": product.url,
                        }
                    )

                conn.execute(
                    """
                    UPDATE ami_products
                    SET name = ?, category = ?, price = ?, availability = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        product.name,
                        product.category,
                        product.price,
                        product.availability,
                        prev["id"],
                    ),
                )
        return updates

    @staticmethod
    def print_report(report: dict[str, list[dict[str, str]]]) -> None:
        print("\n=== AMI.by Price Intelligence Report ===")
        new_items = report.get("new_items", [])
        changes = report.get("price_changes", [])

        print(f"New items: {len(new_items)}")
        for item in new_items:
            print(
                f"  + [{item['category']}] {item['name']} | {item['price']} | {item['url']}"
            )

        print(f"Price changes: {len(changes)}")
        for item in changes:
            print(
                "  * "
                f"[{item['category']}] {item['name']} | "
                f"{item['old_price']} -> {item['new_price']} ({item['change']}) | "
                f"{item['url']}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="AMI.by price tracker")
    parser.add_argument("--db", default="ami_prices.db", help="Path to SQLite DB file")
    args = parser.parse_args()

    tracker = AmiTracker(db_path=args.db)
    products = tracker.get_all_products()
    report = tracker.check_updates(products)
    tracker.print_report(report)


if __name__ == "__main__":
    main()
