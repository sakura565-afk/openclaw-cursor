"""Tests for scripts.sitemap_generator."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from scripts import sitemap_generator as sg


@pytest.fixture
def fixture_root(tmp_path: Path) -> Path:
    products_dir = tmp_path / "data" / "products"
    products_dir.mkdir(parents=True)

    (products_dir / "divan-bergamo.json").write_text(
        json.dumps(
            {
                "slug": "divan-bergamo",
                "category": "divany",
                "name": "Диван Бергамо",
                "lastmod": "2026-05-10",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (products_dir / "kreslo-kapri.json").write_text(
        json.dumps(
            {
                "slug": "kreslo-kapri",
                "category": "kresla",
                "updated_at": "2026-05-12T15:30:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    categories_file = tmp_path / "seo" / "meta_templates.json"
    categories_file.parent.mkdir(parents=True)
    categories_file.write_text(
        json.dumps(
            {
                "categories": {
                    "divany": {
                        "url": "https://divaninfo.ru/katalog/divany/",
                    },
                    "kresla": {
                        "url": "https://divaninfo.ru/katalog/kresla/",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    blog_dir = tmp_path / "blog"
    blog_dir.mkdir()
    (blog_dir / "test-post.md").write_text(
        textwrap.dedent(
            """\
            ---
            title: Test post
            date: 2026-05-13
            ---

            # Test
            """
        ),
        encoding="utf-8",
    )

    return tmp_path


def test_generate_writes_sitemap_and_robots(fixture_root: Path) -> None:
    output_dir = fixture_root / "public"

    sitemap_path, robots_path, count = sg.generate(
        domain="divaninfo.ru",
        output_dir=output_dir,
        products_dir=fixture_root / "data",
        categories_file=fixture_root / "seo" / "meta_templates.json",
        blog_dir=fixture_root / "blog",
    )

    sitemap = sitemap_path.read_text(encoding="utf-8")
    robots = robots_path.read_text(encoding="utf-8")

    assert count == 8
    assert sitemap_path.exists()
    assert robots_path.exists()
    assert "<loc>https://divaninfo.ru/</loc>" in sitemap
    assert "<loc>https://divaninfo.ru/katalog/divany/divan-bergamo/</loc>" in sitemap
    assert "<lastmod>2026-05-10</lastmod>" in sitemap
    assert "<lastmod>2026-05-12</lastmod>" in sitemap
    assert "<priority>1.0</priority>" in sitemap
    assert "<changefreq>daily</changefreq>" in sitemap
    assert "Sitemap: https://divaninfo.ru/sitemap.xml" in robots


def test_product_path_supports_url_path() -> None:
    product = {"url_path": "/katalog/stoly/stol-klassik/", "lastmod": "2026-06-01"}
    assert sg.product_path(product) == "/katalog/stoly/stol-klassik/"
    assert sg.normalize_lastmod("2026-06-01T08:00:00") == "2026-06-01"
