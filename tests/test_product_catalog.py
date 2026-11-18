"""Tests for the product catalog parser pipeline.

Fixtures are embedded inline (as strings) rather than as files because the
repo's ``.gitignore`` excludes ``tests/fixtures/``. Networking is faked via a
tiny in-memory session so no live requests are made.
"""

from __future__ import annotations

import io
import os
import sqlite3
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import pytest
from bs4 import BeautifulSoup

from scripts import product_catalog as pc
from scripts.product_catalog import (
    DiffReport,
    ProductCatalogParser,
    extract_card,
    init_db,
    load_site_config,
    parse_in_stock,
    parse_price,
)
from scripts.product_catalog_diff import diff_alert_message

CONFIG_PATH = Path(pc.DEFAULT_CONFIG_PATH)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

AMADEY_SITEMAP_INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://amadey.ru/sitemap-products.xml</loc></sitemap>
  <sitemap><loc>https://amadey.ru/sitemap-pages.xml</loc></sitemap>
</sitemapindex>
"""

AMADEY_SITEMAP_PRODUCTS = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://amadey.ru/catalog/stoly/stol-dubovyj-premium</loc></url>
  <url><loc>https://amadey.ru/catalog/stoly/</loc></url>
  <url><loc>https://amadey.ru/product/kreslo-massiv</loc></url>
  <url><loc>https://amadey.ru/about/</loc></url>
</urlset>
"""

AMADEY_SITEMAP_PAGES = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://amadey.ru/contacts/</loc></url>
</urlset>
"""

AMADEY_PRODUCT_HTML = """
<html><head></head><body>
  <h1 class="product-title">Стол дубовый Premium</h1>
  <span class="sku">AM-1001</span>
  <meta itemprop="price" content="45990" />
  <div class="in-stock">В наличии</div>
  <div class="product-description">Массив дуба, ручная работа. Прочный обеденный стол.</div>
  <div class="gallery">
    <div class="product-image"><img src="/img/stol-1.jpg" /></div>
    <div class="product-image"><img src="/img/stol-2.jpg" /></div>
    <div class="product-image"><img src="/img/stol-3.jpg" /></div>
  </div>
</body></html>
"""

DIVANINFO_PRODUCT_HTML = """
<html><head></head><body>
  <h1 class="entry-title">Диван угловой Милан</h1>
  <span class="sku">DV-2002</span>
  <p class="price"><span class="amount">89 900 ₽</span></p>
  <div class="stock">Под заказ</div>
  <div class="woocommerce-product-details__short-description">
    Мягкий угловой диван с механизмом еврокнижка.
  </div>
  <div class="woocommerce-product-gallery__image"><img src="https://divaninfo.ru/wp/divan-1.jpg" /></div>
</body></html>
"""

INCOMPLETE_HTML = """
<html><body>
  <span class="sku"></span>
</body></html>
"""

# Legacy base.php-style page: no microdata, price only in the title text,
# SKU must come from the URL `id` param.
LEGACY_AMADEY_HTML = """
<html><head>
  <title>Купить стул «Барокко Люкс» S105. Цена на стул от 15080 рублей.</title>
</head><body>
  <h1>Стул «Барокко Люкс» S105 из массива дерева.</h1>
  <p>У нас можно купить стул по цене от 15080 руб. Доставка по России.</p>
</body></html>
"""


class FakeResponse:
    def __init__(self, text: str = "", status_code: int = 200):
        self.text = text
        self.content = text.encode("utf-8")
        self.status_code = status_code
        self.headers: dict[str, str] = {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise pc.requests.HTTPError(f"status={self.status_code}")


class FakeSession:
    """Maps URLs to :class:`FakeResponse` objects; 404 for anything else."""

    def __init__(self, routes: dict[str, FakeResponse]):
        self.routes = routes
        self.headers: dict[str, str] = {}
        self.calls: list[str] = []

    def get(self, url, timeout=None):
        self.calls.append(url)
        return self.routes.get(url, FakeResponse("", status_code=404))

    def mount(self, *args, **kwargs):  # pragma: no cover - unused in tests
        pass


def make_amadey_parser(tmp_path, routes=None, **kwargs):
    routes = routes or {}
    session = FakeSession(routes)
    return ProductCatalogParser(
        "amadey",
        tmp_path / "catalog.db",
        delay_s=0,
        config_path=CONFIG_PATH,
        session=session,
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# Pure helper tests
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text,expected",
    [
        ("45990", 45990.0),
        ("12 990 ₽", 12990.0),
        ("12\u00a0990 руб.", 12990.0),
        ("1 299,50", 1299.50),
        ("89 900 ₽", 89900.0),
        ("", None),
        (None, None),
        ("нет цены", None),
    ],
)
def test_parse_price(text, expected):
    assert parse_price(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("В наличии", True),
        ("на складе", True),
        ("InStock", True),
        ("Под заказ", False),
        ("нет в наличии", False),
        ("", None),
        (None, None),
        ("что-то непонятное", None),
    ],
)
def test_parse_in_stock(text, expected):
    assert parse_in_stock(text) == expected


def test_robots_policy_disallow():
    text = "User-agent: *\nDisallow: /admin/\nDisallow: /cart\n"
    policy = pc.RobotsPolicy.from_text(text, pc.DEFAULT_USER_AGENT)
    assert policy.allowed("https://amadey.ru/catalog/stoly/stol-1") is True
    assert policy.allowed("https://amadey.ru/admin/login") is False
    assert policy.allowed("https://amadey.ru/cart") is False


# --------------------------------------------------------------------------- #
# discover_urls
# --------------------------------------------------------------------------- #


def test_discover_urls_follows_index_and_filters(tmp_path):
    routes = {
        "https://amadey.ru/sitemap.xml": FakeResponse(AMADEY_SITEMAP_INDEX),
        "https://amadey.ru/sitemap-products.xml": FakeResponse(AMADEY_SITEMAP_PRODUCTS),
        "https://amadey.ru/sitemap-pages.xml": FakeResponse(AMADEY_SITEMAP_PAGES),
    }
    parser = make_amadey_parser(tmp_path, routes)
    urls = parser.discover_urls()
    assert urls == [
        "https://amadey.ru/catalog/stoly/stol-dubovyj-premium",
        "https://amadey.ru/product/kreslo-massiv",
    ]


def test_discover_urls_respects_max_pages(tmp_path):
    routes = {
        "https://amadey.ru/sitemap.xml": FakeResponse(AMADEY_SITEMAP_INDEX),
        "https://amadey.ru/sitemap-products.xml": FakeResponse(AMADEY_SITEMAP_PRODUCTS),
        "https://amadey.ru/sitemap-pages.xml": FakeResponse(AMADEY_SITEMAP_PAGES),
    }
    parser = make_amadey_parser(tmp_path, routes, max_pages=1)
    urls = parser.discover_urls()
    assert len(urls) == 1


# --------------------------------------------------------------------------- #
# extract_card
# --------------------------------------------------------------------------- #


def test_extract_card_amadey():
    soup = BeautifulSoup(AMADEY_PRODUCT_HTML, "lxml")
    url = "https://amadey.ru/catalog/stoly/stol-dubovyj-premium"
    card = extract_card(soup, url, "amadey")
    assert card.sku == "AM-1001"
    assert card.title == "Стол дубовый Premium"
    assert card.price_rub == 45990.0
    assert card.in_stock is True
    assert card.category == "stoly"
    assert card.image_main_url == "https://amadey.ru/img/stol-1.jpg"
    assert card.image_count == 3
    assert card.is_complete() is True


def test_extract_card_divaninfo():
    soup = BeautifulSoup(DIVANINFO_PRODUCT_HTML, "lxml")
    url = "https://divaninfo.ru/divan/uglovoy-milan"
    card = extract_card(soup, url, "divaninfo")
    assert card.sku == "DV-2002"
    assert card.title == "Диван угловой Милан"
    assert card.price_rub == 89900.0
    assert card.in_stock is False
    assert card.category == "divan"
    assert card.image_main_url == "https://divaninfo.ru/wp/divan-1.jpg"
    assert card.is_complete() is True


def test_extract_card_incomplete_is_not_complete():
    soup = BeautifulSoup(INCOMPLETE_HTML, "lxml")
    card = extract_card(soup, "https://amadey.ru/product/x", "amadey")
    assert card.is_complete() is False


def test_extract_card_legacy_fallbacks():
    """SKU from URL param + price from page-text regex (no microdata)."""
    soup = BeautifulSoup(LEGACY_AMADEY_HTML, "lxml")
    url = "https://amadey.ru/base.php?tip=1&id=86&type=1"
    card = extract_card(soup, url, "amadey")
    assert card.sku == "86"
    assert card.title.startswith("Стул «Барокко Люкс»")
    assert card.price_rub == 15080.0
    assert card.is_complete() is True


# --------------------------------------------------------------------------- #
# fetch_card
# --------------------------------------------------------------------------- #


def test_fetch_card_ok(tmp_path):
    product_url = "https://amadey.ru/catalog/stoly/stol-dubovyj-premium"
    routes = {
        "https://amadey.ru/robots.txt": FakeResponse("User-agent: *\nDisallow: /admin/\n"),
        product_url: FakeResponse(AMADEY_PRODUCT_HTML),
    }
    parser = make_amadey_parser(tmp_path, routes)
    card = parser.fetch_card(product_url)
    assert card is not None
    assert card.sku == "AM-1001"


def test_fetch_card_404_returns_none(tmp_path):
    routes = {
        "https://amadey.ru/robots.txt": FakeResponse("User-agent: *\n"),
    }
    parser = make_amadey_parser(tmp_path, routes)
    assert parser.fetch_card("https://amadey.ru/product/missing") is None


def test_fetch_card_incomplete_returns_none(tmp_path):
    url = "https://amadey.ru/product/incomplete"
    routes = {
        "https://amadey.ru/robots.txt": FakeResponse("User-agent: *\n"),
        url: FakeResponse(INCOMPLETE_HTML),
    }
    parser = make_amadey_parser(tmp_path, routes)
    assert parser.fetch_card(url) is None


# --------------------------------------------------------------------------- #
# diff_since_last with a pre-populated DB
# --------------------------------------------------------------------------- #


def _seed_history(conn, site, run_id, rows):
    conn.execute(
        "INSERT INTO runs (id, site, started_at) VALUES (?, ?, ?)",
        (run_id, site, "2026-01-01T00:00:00+00:00"),
    )
    for sku, price, in_stock, title in rows:
        conn.execute(
            "INSERT INTO product_history (sku, site, run_id, price_rub, in_stock, title, captured_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sku, site, run_id, price, in_stock, title, "2026-01-01T00:00:00+00:00"),
        )
    conn.commit()


def test_diff_since_last(tmp_path):
    db_path = tmp_path / "catalog.db"
    conn = init_db(db_path)
    _seed_history(conn, "amadey", 1, [
        ("A1", 1000.0, 1, "Alpha"),
        ("A2", 2000.0, 1, "Beta"),
        ("A3", 3000.0, 1, "Gamma"),
    ])
    _seed_history(conn, "amadey", 2, [
        ("A1", 1000.0, 1, "Alpha"),      # unchanged
        ("A2", 2500.0, 1, "Beta"),       # price changed
        ("A4", 4000.0, 1, "Delta"),      # added (A3 removed)
    ])
    conn.close()

    parser = ProductCatalogParser("amadey", db_path, delay_s=0, config_path=CONFIG_PATH,
                                  session=FakeSession({}))
    diff = parser.diff_since_last()
    assert diff.prev_run_id == 1
    assert diff.curr_run_id == 2
    assert [a["sku"] for a in diff.added] == ["A4"]
    assert [r["sku"] for r in diff.removed] == ["A3"]
    assert len(diff.changed) == 1
    assert diff.changed[0]["sku"] == "A2"
    assert diff.changed[0]["changes"]["price_rub"] == {"old": 2000.0, "new": 2500.0}
    assert diff.unchanged == ["A1"]


def test_diff_since_run_argument(tmp_path):
    db_path = tmp_path / "catalog.db"
    conn = init_db(db_path)
    _seed_history(conn, "amadey", 1, [("A1", 1000.0, 1, "Alpha")])
    _seed_history(conn, "amadey", 2, [("A1", 1500.0, 1, "Alpha")])
    conn.close()
    parser = ProductCatalogParser("amadey", db_path, delay_s=0, config_path=CONFIG_PATH,
                                  session=FakeSession({}))
    diff = parser.diff_since_last(since_run=1)
    assert diff.curr_run_id == 1
    assert diff.prev_run_id is None
    assert len(diff.added) == 1


# --------------------------------------------------------------------------- #
# Full run() with faked HTTP + persistence
# --------------------------------------------------------------------------- #


def _full_routes():
    p1 = "https://amadey.ru/catalog/stoly/stol-dubovyj-premium"
    p2 = "https://amadey.ru/product/kreslo-massiv"
    return {
        "https://amadey.ru/robots.txt": FakeResponse("User-agent: *\nDisallow: /admin/\n"),
        "https://amadey.ru/sitemap.xml": FakeResponse(AMADEY_SITEMAP_INDEX),
        "https://amadey.ru/sitemap-products.xml": FakeResponse(AMADEY_SITEMAP_PRODUCTS),
        "https://amadey.ru/sitemap-pages.xml": FakeResponse(AMADEY_SITEMAP_PAGES),
        p1: FakeResponse(AMADEY_PRODUCT_HTML),
        p2: FakeResponse(AMADEY_PRODUCT_HTML.replace("AM-1001", "AM-1002")),
    }


def test_run_persists_products(tmp_path):
    parser = make_amadey_parser(tmp_path, _full_routes())
    summary = parser.run()
    assert summary.urls_discovered == 2
    assert summary.urls_fetched_ok == 2
    assert summary.urls_failed == 0
    assert summary.error is None

    conn = sqlite3.connect(parser.db_path)
    conn.row_factory = sqlite3.Row
    products = conn.execute("SELECT * FROM products WHERE is_current = 1").fetchall()
    assert len(products) == 2
    history = conn.execute("SELECT COUNT(*) AS n FROM product_history").fetchone()["n"]
    assert history == 2
    conn.close()


def test_run_dry_run_does_not_persist(tmp_path):
    parser = make_amadey_parser(tmp_path, _full_routes(), dry_run=True)
    summary = parser.run()
    assert summary.urls_discovered == 2
    assert summary.urls_fetched_ok == 0
    assert not parser.db_path.exists()


# --------------------------------------------------------------------------- #
# CSV export
# --------------------------------------------------------------------------- #


def test_export_csv_matches_db(tmp_path):
    parser = make_amadey_parser(tmp_path, _full_routes())
    parser.run()

    out = tmp_path / "out.csv"
    count = parser.export_csv(out)
    assert count == 2

    content = out.read_text(encoding="utf-8").strip().splitlines()
    header = content[0].split(",")
    assert header == [
        "sku", "title", "category", "price_rub", "in_stock",
        "description_short", "image_main_url", "image_count", "url",
        "first_seen_run_id", "last_seen_run_id",
    ]
    assert len(content) - 1 == count  # rows match DB count


# --------------------------------------------------------------------------- #
# diff_alert_message thresholds
# --------------------------------------------------------------------------- #


def test_diff_alert_message_below_threshold_returns_none():
    diff = DiffReport(site="amadey", prev_run_id=1, curr_run_id=2,
                      added=[{"sku": "A", "title": "A", "price_rub": 1.0}])
    assert diff_alert_message("amadey", diff, threshold_added=5) is None


def test_diff_alert_message_added_threshold():
    added = [{"sku": f"A{i}", "title": f"Prod {i}", "price_rub": 100.0 * i} for i in range(6)]
    diff = DiffReport(site="amadey", prev_run_id=1, curr_run_id=2, added=added)
    message = diff_alert_message("amadey", diff, threshold_added=5)
    assert message is not None
    assert "New products" in message
    assert "amadey" in message


def test_diff_alert_message_price_move():
    diff = DiffReport(
        site="amadey", prev_run_id=1, curr_run_id=2,
        changed=[{"sku": "A1", "changes": {"price_rub": {"old": 1000.0, "new": 1500.0}}}],
    )
    message = diff_alert_message("amadey", diff, threshold_added=100,
                                 threshold_price_change_pct=10.0)
    assert message is not None
    assert "Price moves" in message
    assert "A1" in message


# --------------------------------------------------------------------------- #
# CLI smoke tests
# --------------------------------------------------------------------------- #


def test_cli_parse_dry_run_exits_zero(tmp_path):
    routes = _full_routes()

    def fake_session_factory(self):
        return FakeSession(routes)

    with mock.patch.object(ProductCatalogParser, "_build_session", fake_session_factory):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = pc.main([
                "--db", str(tmp_path / "catalog.db"),
                "--config", str(CONFIG_PATH),
                "parse", "--site", "amadey", "--dry-run", "--max-pages", "5",
                "--delay", "0",
            ])
    assert code == 0
    assert "https://amadey.ru/catalog/stoly/stol-dubovyj-premium" in stdout.getvalue()


def test_cli_export(tmp_path):
    parser = make_amadey_parser(tmp_path, _full_routes())
    parser.run()
    out = tmp_path / "export.csv"
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        code = pc.main([
            "--db", str(parser.db_path),
            "--config", str(CONFIG_PATH),
            "export", "--site", "amadey", "--out", str(out),
        ])
    assert code == 0
    assert out.exists()
    assert len(out.read_text(encoding="utf-8").splitlines()) - 1 == 2


def test_cli_diff_json(tmp_path):
    parser = make_amadey_parser(tmp_path, _full_routes())
    parser.run()
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        code = pc.main([
            "--db", str(parser.db_path),
            "--config", str(CONFIG_PATH),
            "diff", "--site", "amadey", "--json",
        ])
    assert code == 0
    assert '"site": "amadey"' in stdout.getvalue()


def test_unknown_site_raises():
    with pytest.raises(KeyError):
        load_site_config("nonexistent", CONFIG_PATH)
