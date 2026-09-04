#!/usr/bin/env python3
"""Generate sitemap.xml and robots.txt for divaninfo.ru.

Scans product JSON files, category metadata, and blog markdown posts,
then writes SEO files into the public output directory.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
DEFAULT_DOMAIN = "https://divaninfo.ru"
REPO_ROOT = Path(__file__).resolve().parent.parent

SECTION_CONFIG = {
    "home": {"priority": "1.0", "changefreq": "daily"},
    "category": {"priority": "0.8", "changefreq": "weekly"},
    "product": {"priority": "0.6", "changefreq": "monthly"},
    "blog": {"priority": "0.7", "changefreq": "weekly"},
}

FRONTMATTER_RE = re.compile(r"^---\s*\r?\n(.*?)\r?\n---", re.DOTALL)
DATE_FIELD_RE = re.compile(r"^date:\s*(.+?)\s*$", re.MULTILINE)
CATEGORY_SLUG_RE = re.compile(r"/katalog/([^/]+)/?")


@dataclass(frozen=True)
class SitemapEntry:
    loc: str
    changefreq: str
    priority: str
    lastmod: str | None = None


def normalize_domain(domain: str) -> str:
    domain = domain.strip().rstrip("/")
    if not domain.startswith(("http://", "https://")):
        domain = f"https://{domain}"
    return domain


def normalize_lastmod(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    if "T" in text:
        text = text.split("T", 1)[0]
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return None


def join_url(domain: str, path: str) -> str:
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{domain}{path}"


def category_slug_from_url(url: str) -> str | None:
    match = CATEGORY_SLUG_RE.search(url)
    return match.group(1) if match else None


def load_categories(categories_file: Path, domain: str) -> list[SitemapEntry]:
    if not categories_file.is_file():
        return []

    payload = json.loads(categories_file.read_text(encoding="utf-8"))
    entries: list[SitemapEntry] = []
    seen: set[str] = set()
    config = SECTION_CONFIG["category"]

    for info in payload.get("categories", {}).values():
        slug = category_slug_from_url(str(info.get("url", "")))
        if not slug or slug in seen:
            continue
        seen.add(slug)
        entries.append(
            SitemapEntry(
                loc=join_url(domain, f"/katalog/{slug}/"),
                changefreq=config["changefreq"],
                priority=config["priority"],
            )
        )

    entries.sort(key=lambda item: item.loc)
    return entries


def parse_product_records(payload: Any, source: Path) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        raise ValueError(f"Unsupported JSON structure in {source}")

    if isinstance(payload.get("products"), list):
        return [item for item in payload["products"] if isinstance(item, dict)]
    if payload.get("slug") or payload.get("url_path"):
        return [payload]
    return []


def product_lastmod(product: dict[str, Any], source: Path) -> str | None:
    for key in ("lastmod", "updated_at", "modified", "date"):
        lastmod = normalize_lastmod(product.get(key))
        if lastmod:
            return lastmod
    try:
        return datetime.fromtimestamp(source.stat().st_mtime).date().isoformat()
    except OSError:
        return None


def product_path(product: dict[str, Any]) -> str | None:
    url_path = product.get("url_path") or product.get("path")
    if url_path:
        path = str(url_path).strip()
        if not path.startswith("/"):
            path = f"/{path}"
        return path if path.endswith("/") else f"{path}/"

    slug = product.get("slug")
    category = product.get("category")
    if not slug or not category:
        return None

    category_slug = str(category).replace("_", "-")
    product_slug = str(slug).strip("/")
    return f"/katalog/{category_slug}/{product_slug}/"


def load_products(products_dir: Path) -> list[SitemapEntry]:
    if not products_dir.is_dir():
        return []

    config = SECTION_CONFIG["product"]
    entries: list[SitemapEntry] = []
    seen_paths: set[str] = set()
    files = sorted(products_dir.rglob("*.json"))

    catalog_file = products_dir / "products.json"
    if catalog_file.is_file():
        files = [path for path in files if path != catalog_file]
        files.insert(0, catalog_file)

    for source in files:
        if source.name in {"categories.json", "meta_templates.json"}:
            continue
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"warning: skip {source}: {exc}", file=sys.stderr)
            continue

        try:
            records = parse_product_records(payload, source)
        except ValueError as exc:
            print(f"warning: {exc}", file=sys.stderr)
            continue

        for product in records:
            path = product_path(product)
            if not path or path in seen_paths:
                continue
            seen_paths.add(path)
            entries.append(
                SitemapEntry(
                    loc=path,
                    changefreq=config["changefreq"],
                    priority=config["priority"],
                    lastmod=product_lastmod(product, source),
                )
            )

    entries.sort(key=lambda item: item.loc)
    return entries


def blog_lastmod(post_file: Path) -> str | None:
    try:
        text = post_file.read_text(encoding="utf-8")
    except OSError:
        return None

    match = FRONTMATTER_RE.match(text)
    if match:
        date_match = DATE_FIELD_RE.search(match.group(1))
        if date_match:
            lastmod = normalize_lastmod(date_match.group(1))
            if lastmod:
                return lastmod

    try:
        return datetime.fromtimestamp(post_file.stat().st_mtime).date().isoformat()
    except OSError:
        return None


def load_blog_posts(blog_dir: Path, domain: str) -> list[SitemapEntry]:
    if not blog_dir.is_dir():
        return []

    config = SECTION_CONFIG["blog"]
    entries = [
        SitemapEntry(
            loc=join_url(domain, "/blog/"),
            changefreq=config["changefreq"],
            priority=config["priority"],
        )
    ]

    for post_file in sorted(blog_dir.glob("*.md")):
        entries.append(
            SitemapEntry(
                loc=join_url(domain, f"/blog/{post_file.stem}/"),
                changefreq=config["changefreq"],
                priority=config["priority"],
                lastmod=blog_lastmod(post_file),
            )
        )

    return entries


def build_entries(
    domain: str,
    products_dir: Path,
    categories_file: Path,
    blog_dir: Path,
) -> list[SitemapEntry]:
    home = SECTION_CONFIG["home"]
    entries: list[SitemapEntry] = [
        SitemapEntry(
            loc=join_url(domain, "/"),
            changefreq=home["changefreq"],
            priority=home["priority"],
        ),
        SitemapEntry(
            loc=join_url(domain, "/katalog/"),
            changefreq=SECTION_CONFIG["category"]["changefreq"],
            priority=SECTION_CONFIG["category"]["priority"],
        ),
    ]

    entries.extend(load_categories(categories_file, domain))

    product_entries = load_products(products_dir)
    for entry in product_entries:
        entries.append(
            SitemapEntry(
                loc=join_url(domain, entry.loc),
                changefreq=entry.changefreq,
                priority=entry.priority,
                lastmod=entry.lastmod,
            )
        )

    entries.extend(load_blog_posts(blog_dir, domain))
    return entries


def render_sitemap_xml(entries: list[SitemapEntry]) -> str:
    ET.register_namespace("", SITEMAP_NS)
    urlset = ET.Element(f"{{{SITEMAP_NS}}}urlset")

    for entry in entries:
        url = ET.SubElement(urlset, f"{{{SITEMAP_NS}}}url")
        ET.SubElement(url, f"{{{SITEMAP_NS}}}loc").text = entry.loc
        if entry.lastmod:
            ET.SubElement(url, f"{{{SITEMAP_NS}}}lastmod").text = entry.lastmod
        ET.SubElement(url, f"{{{SITEMAP_NS}}}changefreq").text = entry.changefreq
        ET.SubElement(url, f"{{{SITEMAP_NS}}}priority").text = entry.priority

    ET.indent(urlset, space="  ")
    body = ET.tostring(urlset, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{body}\n'


def render_robots_txt(domain: str) -> str:
    return "\n".join(
        [
            "User-agent: *",
            "Allow: /",
            "",
            f"Sitemap: {join_url(domain, '/sitemap.xml')}",
            "",
        ]
    )


def write_outputs(output_dir: Path, sitemap_xml: str, robots_txt: str) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    sitemap_path = output_dir / "sitemap.xml"
    robots_path = output_dir / "robots.txt"
    sitemap_path.write_text(sitemap_xml, encoding="utf-8")
    robots_path.write_text(robots_txt, encoding="utf-8")
    return sitemap_path, robots_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate sitemap.xml and robots.txt for divaninfo.ru",
    )
    parser.add_argument(
        "--domain",
        default=DEFAULT_DOMAIN,
        help=f"Site domain (default: {DEFAULT_DOMAIN})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "public",
        help="Output directory for sitemap.xml and robots.txt (default: public/)",
    )
    parser.add_argument(
        "--products-dir",
        type=Path,
        default=REPO_ROOT / "data",
        help="Directory with product JSON files (default: data/)",
    )
    parser.add_argument(
        "--categories-file",
        type=Path,
        default=REPO_ROOT / "seo" / "meta_templates.json",
        help="Category metadata JSON (default: seo/meta_templates.json)",
    )
    parser.add_argument(
        "--blog-dir",
        type=Path,
        default=REPO_ROOT / "blog",
        help="Blog markdown directory (default: blog/)",
    )
    return parser


def generate(
    domain: str,
    output_dir: Path,
    products_dir: Path,
    categories_file: Path,
    blog_dir: Path,
) -> tuple[Path, Path, int]:
    normalized_domain = normalize_domain(domain)
    entries = build_entries(normalized_domain, products_dir, categories_file, blog_dir)
    sitemap_xml = render_sitemap_xml(entries)
    robots_txt = render_robots_txt(normalized_domain)
    sitemap_path, robots_path = write_outputs(output_dir, sitemap_xml, robots_txt)
    return sitemap_path, robots_path, len(entries)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    sitemap_path, robots_path, count = generate(
        domain=args.domain,
        output_dir=args.output,
        products_dir=args.products_dir,
        categories_file=args.categories_file,
        blog_dir=args.blog_dir,
    )

    print(f"Wrote {count} URLs to {sitemap_path}")
    print(f"Wrote robots.txt to {robots_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
