"""Tests for scripts/image_audit.py and scripts/image_audit_report.py.

No live network is used: page HTML is provided as canned fixtures and
``head_image`` is mocked via ``unittest.mock``.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
# Repo root lets us import the ``scripts`` package (so coverage can target
# ``scripts.image_audit``); the scripts dir keeps sibling imports inside the
# modules (e.g. ``from image_audit_report import ...``) working too.
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

from scripts import image_audit as ia  # noqa: E402
from scripts import image_audit_report as iar  # noqa: E402


# --------------------------------------------------------------------------
# Canned fixtures
# --------------------------------------------------------------------------
AMADEY_HTML = """
<!DOCTYPE html>
<html lang="ru"><head><title>Диваны — Амадей</title></head>
<body>
  <header>
    <a href="/"><img src="/logo.png" alt="Амадей"></a>
  </header>
  <main>
    <!-- good hero image: alt, dimensions, eager above fold -->
    <img src="/img/hero.jpg" alt="Диван Классик" width="1200" height="600" loading="eager">
    <!-- missing alt, missing dimensions, oversized png -->
    <a href="/katalog/divany/klassik/"><img src="/img/klassik.png"></a>
    <!-- picture/source with keyword-stuffed alt -->
    <picture>
      <source srcset="/img/sofa.webp 1x, /img/sofa@2x.webp 2x" type="image/webp">
      <img src="/img/sofa.jpg" alt="диван, кресло, стул, стол, комод, тумба, полка, шкаф, зеркало"
           width="800" height="500">
    </picture>
    <!-- empty src placeholder -->
    <img src="#" alt="placeholder">
    <!-- below the fold, missing lazy -->
    <img src="/img/footer-banner.jpg" alt="Баннер" width="300" height="120">
  </main>
</body></html>
"""

DIVANINFO_HTML = """
<!DOCTYPE html>
<html lang="ru"><head><title>Каталог — DivanInfo</title></head>
<body>
  <img src="/media/banner.png" alt="" width="1000" height="300" loading="lazy">
  <img src="https://cdn.divaninfo.ru/p/1.webp" alt="Прямой диван" width="600" height="400">
  <img src="/media/broken.jpg" alt="Битая картинка" width="400" height="300">
  <img src="/media/verylong.jpg"
       alt="Очень длинное альтернативное описание изображения которое явно превышает сто пятьдесят символов и продолжает описывать диван снова и снова без остановки подробно"
       width="500" height="500">
</body></html>
"""


# --------------------------------------------------------------------------
# extract_images
# --------------------------------------------------------------------------
def test_extract_images_counts_and_attrs():
    imgs = ia.extract_images(AMADEY_HTML, "https://amadey.ru/katalog/divany/")
    assert len(imgs) == 6  # logo + hero + klassik + sofa + placeholder + footer

    by_src = {img.src.rsplit("/", 1)[-1]: img for img in imgs}
    hero = by_src["hero.jpg"]
    assert hero.alt == "Диван Классик"
    assert hero.width == 1200 and hero.height == 600
    assert hero.loading == "eager"
    assert hero.fmt == "jpg"

    # picture/source srcset captured onto the img record
    sofa = by_src["sofa.jpg"]
    assert "sofa.webp" in sofa.srcset

    # placeholder src preserved (not resolved to page URL)
    assert by_src["#"].src == "#" or any(i.src == "#" for i in imgs)


def test_extract_detects_link_context():
    imgs = ia.extract_images(AMADEY_HTML, "https://amadey.ru/katalog/divany/")
    klassik = next(i for i in imgs if i.src.endswith("klassik.png"))
    assert klassik.in_link is True


# --------------------------------------------------------------------------
# audit_image
# --------------------------------------------------------------------------
def _audit(img, **kw):
    return ia.audit_image(
        img,
        thresholds=ia.DEFAULT_THRESHOLDS,
        severity=ia.DEFAULT_SEVERITY,
        **kw,
    )


def test_audit_missing_alt():
    img = ia.ImageRecord(page_url="p", src="https://x/y.jpg", alt_present=False,
                         width=10, height=10, fmt="jpg", above_fold=False)
    types = {i.type for i in _audit(img)}
    assert "MISSING_ALT" in types


def test_audit_empty_src_short_circuits():
    img = ia.ImageRecord(page_url="p", src="#", alt="x", alt_present=True)
    issues = _audit(img)
    assert [i.type for i in issues] == ["EMPTY_SRC"]
    assert issues[0].severity == "critical"


def test_audit_broken_url():
    img = ia.ImageRecord(page_url="p", src="https://x/y.jpg", alt="ok",
                         alt_present=True, width=1, height=1, fmt="jpg",
                         status_code=404, above_fold=True, loading="eager")
    types = {i.type for i in _audit(img)}
    assert "BROKEN_URL" in types


def test_audit_oversize_png():
    img = ia.ImageRecord(page_url="p", src="https://x/a.png", alt="ok",
                         alt_present=True, width=1, height=1, fmt="png",
                         file_size_kb=350, above_fold=True, loading="eager")
    types = {i.type for i in _audit(img)}
    assert "OVERSIZE_FILE" in types
    assert "UNOPTIMIZED_FORMAT" in types  # png => unoptimized


def test_audit_webp_under_budget_not_oversize():
    img = ia.ImageRecord(page_url="p", src="https://x/a.webp", alt="ok",
                         alt_present=True, width=1, height=1, fmt="webp",
                         file_size_kb=400, above_fold=True, loading="eager")
    types = {i.type for i in _audit(img)}
    assert "OVERSIZE_FILE" not in types


def test_audit_missing_dimensions():
    img = ia.ImageRecord(page_url="p", src="https://x/a.jpg", alt="ok",
                         alt_present=True, fmt="jpg", above_fold=True,
                         loading="eager")
    types = {i.type for i in _audit(img)}
    assert "MISSING_DIMENSIONS" in types


def test_audit_alt_too_long():
    long_alt = "д" * 200
    img = ia.ImageRecord(page_url="p", src="https://x/a.jpg", alt=long_alt,
                         alt_present=True, width=1, height=1, fmt="jpg",
                         above_fold=True, loading="eager")
    types = {i.type for i in _audit(img)}
    assert "ALT_TOO_LONG" in types


def test_audit_alt_keyword_stuffed():
    alt = "диван, кресло, стул, стол, комод, тумба"
    img = ia.ImageRecord(page_url="p", src="https://x/a.jpg", alt=alt,
                         alt_present=True, width=1, height=1, fmt="jpg",
                         above_fold=True, loading="eager")
    types = {i.type for i in _audit(img)}
    assert "ALT_KEYWORD_STUFFED" in types


def test_audit_lazy_above_fold():
    img = ia.ImageRecord(page_url="p", src="https://x/a.jpg", alt="ok",
                         alt_present=True, width=1, height=1, fmt="jpg",
                         above_fold=True, loading="lazy")
    types = {i.type for i in _audit(img)}
    assert "LAZY_MISSING_ABOVE_FOLD" in types


def test_audit_below_fold_missing_lazy():
    img = ia.ImageRecord(page_url="p", src="https://x/a.jpg", alt="ok",
                         alt_present=True, width=1, height=1, fmt="jpg",
                         above_fold=False, loading=None)
    types = {i.type for i in _audit(img)}
    assert "LAZY_MISSING_ABOVE_FOLD" in types


def test_audit_duplicate_hash():
    img = ia.ImageRecord(page_url="p", src="https://x/shared.jpg", alt="ok",
                         alt_present=True, width=1, height=1, fmt="jpg",
                         above_fold=False, loading="lazy")
    types = {i.type for i in _audit(img, duplicate_srcs={"https://x/shared.jpg"})}
    assert "DUPLICATE_HASH" in types


def test_audit_clean_image_has_no_issues():
    img = ia.ImageRecord(page_url="p", src="https://x/clean.webp", alt="Хороший диван",
                         alt_present=True, width=800, height=600, fmt="webp",
                         file_size_kb=120, above_fold=True, loading="eager",
                         status_code=200)
    assert _audit(img) == []


# --------------------------------------------------------------------------
# format helpers / sniffing
# --------------------------------------------------------------------------
def test_format_from_url():
    assert ia.format_from_url("https://x/a.JPG?v=2") == "jpg"
    assert ia.format_from_url("https://x/a.webp") == "webp"
    assert ia.format_from_url("https://x/noext") == "unknown"


def test_format_from_content_type():
    assert ia.format_from_content_type("image/jpeg") == "jpg"
    assert ia.format_from_content_type("image/webp; charset=x") == "webp"
    assert ia.format_from_content_type("text/html") == "unknown"


def test_sniff_format_signatures():
    assert ia.sniff_format(b"\xff\xd8\xff\xe0rest") == "jpg"
    assert ia.sniff_format(b"\x89PNG\r\n\x1a\nrest") == "png"
    assert ia.sniff_format(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "webp"
    assert ia.sniff_format(b"") == "unknown"


# --------------------------------------------------------------------------
# head_image (mocked, no network)
# --------------------------------------------------------------------------
def _make_bot(tmp_path, monkeypatch):
    # Avoid robots.txt network fetch during construction.
    monkeypatch.setattr(ia.ImageAuditBot, "_load_robots", lambda self: None)
    return ia.ImageAuditBot("amadey", max_pages=10, delay_s=0.0)


def test_head_image_uses_head(tmp_path, monkeypatch):
    bot = _make_bot(tmp_path, monkeypatch)
    resp = mock.Mock()
    resp.status_code = 200
    resp.headers = {"Content-Type": "image/jpeg", "Content-Length": "204800"}
    with mock.patch.object(bot.session, "head", return_value=resp) as mh:
        status, ct, length = bot.head_image("https://x/a.jpg")
    assert status == 200 and ct == "image/jpeg" and length == 204800
    mh.assert_called_once()


def test_head_image_falls_back_to_ranged_get(tmp_path, monkeypatch):
    bot = _make_bot(tmp_path, monkeypatch)
    head_resp = mock.Mock()
    head_resp.status_code = 405  # method not allowed
    head_resp.headers = {}
    get_resp = mock.Mock()
    get_resp.status_code = 206
    get_resp.headers = {
        "Content-Type": "image/webp",
        "Content-Range": "bytes 0-65535/512000",
    }
    get_resp.raw = None
    get_resp.close = mock.Mock()
    with mock.patch.object(bot.session, "head", return_value=head_resp), \
         mock.patch.object(bot.session, "get", return_value=get_resp):
        status, ct, length = bot.head_image("https://x/a.webp")
    assert status == 206 and ct == "image/webp" and length == 512000


def test_head_image_network_error_returns_zero(tmp_path, monkeypatch):
    bot = _make_bot(tmp_path, monkeypatch)
    with mock.patch.object(bot.session, "head",
                           side_effect=ia.requests.RequestException("boom")), \
         mock.patch.object(bot.session, "get",
                           side_effect=ia.requests.RequestException("boom")):
        status, ct, length = bot.head_image("https://x/a.jpg")
    assert status == 0 and length == 0


# --------------------------------------------------------------------------
# discover / crawl (mocked session)
# --------------------------------------------------------------------------
SITEMAP_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://amadey.ru/</loc></url>
  <url><loc>https://amadey.ru/divany.htm</loc></url>
  <url><loc>https://amadey.ru/kresla.html</loc></url>
  <url><loc>https://amadey.ru/admin/secret.htm</loc></url>
  <url><loc>https://amadey.ru/cart.htm</loc></url>
</urlset>
"""


def test_discover_pages_filters_scope(tmp_path, monkeypatch):
    bot = _make_bot(tmp_path, monkeypatch)
    resp = mock.Mock()
    resp.status_code = 200
    resp.content = SITEMAP_XML
    with mock.patch.object(bot.session, "get", return_value=resp):
        pages = bot.discover_pages()
    assert "https://amadey.ru/" in pages
    assert "https://amadey.ru/divany.htm" in pages
    assert "https://amadey.ru/kresla.html" in pages
    assert all("/admin/" not in p and "cart" not in p for p in pages)


def test_crawl_pages_extracts_images(tmp_path, monkeypatch):
    bot = _make_bot(tmp_path, monkeypatch)
    resp = mock.Mock()
    resp.status_code = 200
    resp.text = AMADEY_HTML
    with mock.patch.object(bot.session, "get", return_value=resp):
        result = bot.crawl_pages(["https://amadey.ru/katalog/divany/"])
    assert result.site == "amadey"
    assert len(result.images) == 6


# --------------------------------------------------------------------------
# Full run pipeline (mocked network + temp DB)
# --------------------------------------------------------------------------
def _run_bot(tmp_path, monkeypatch, html=AMADEY_HTML):
    bot = _make_bot(tmp_path, monkeypatch)

    page_resp = mock.Mock()
    page_resp.status_code = 200
    page_resp.text = html
    monkeypatch.setattr(bot.session, "get", lambda *a, **k: page_resp)

    def fake_head(url, timeout=5):
        if "broken" in url:
            return (404, "image/jpeg", 0)
        if url.endswith(".png"):
            return (200, "image/png", 350 * 1024)
        return (200, "image/jpeg", 120 * 1024)

    monkeypatch.setattr(bot, "head_image", fake_head)

    db_path = tmp_path / "audit.db"
    out_root = tmp_path / "reports"
    monkeypatch.setattr(ia, "REPORTS_DIR", out_root)
    report = bot.run(
        pages=["https://amadey.ru/katalog/divany/"],
        db_path=db_path,
        write_report=True,
    )
    return bot, report, db_path


def test_run_pipeline_and_db(tmp_path, monkeypatch):
    _bot, report, db_path = _run_bot(tmp_path, monkeypatch)
    assert report.images_total == 6
    assert report.issues_total > 0
    assert report.run_id == 1
    assert report.report_dir is not None
    assert (Path(report.report_dir) / "index.html").exists()
    assert (Path(report.report_dir) / "assets" / "site.css").exists()

    # DB round-trip
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    runs = conn.execute("SELECT COUNT(*) c FROM runs").fetchone()["c"]
    imgs = conn.execute("SELECT COUNT(*) c FROM images").fetchone()["c"]
    issues = conn.execute("SELECT COUNT(*) c FROM issues").fetchone()["c"]
    conn.close()
    assert runs == 1
    assert imgs == 6
    assert issues == report.issues_total


def test_db_schema_inits_cleanly(tmp_path):
    db_path = tmp_path / "schema.db"
    conn = ia.connect_db(db_path)
    ia.init_db(conn)
    names = {
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"runs", "images", "issues"}.issubset(names)
    index_names = {
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    }
    assert "idx_issues_run_type" in index_names
    assert "idx_issues_run_severity" in index_names
    conn.close()


def test_load_last_report(tmp_path, monkeypatch):
    _bot, report, db_path = _run_bot(tmp_path, monkeypatch)
    loaded = ia.load_last_report("amadey", db_path=db_path)
    assert loaded is not None
    assert loaded.images_total == report.images_total
    assert loaded.issues_total == report.issues_total


# --------------------------------------------------------------------------
# HTML report generation
# --------------------------------------------------------------------------
def _make_simple_report():
    img1 = ia.ImageRecord(page_url="https://amadey.ru/a", src="https://x/1.png",
                          alt_present=False, fmt="png", above_fold=True)
    img1.issues = ia.audit_image(img1, ia.DEFAULT_THRESHOLDS, ia.DEFAULT_SEVERITY)
    img2 = ia.ImageRecord(page_url="https://amadey.ru/b", src="https://x/2.jpg",
                          alt="ok", alt_present=True, width=1, height=1, fmt="jpg",
                          above_fold=True, loading="eager", status_code=404)
    img2.issues = ia.audit_image(img2, ia.DEFAULT_THRESHOLDS, ia.DEFAULT_SEVERITY)
    bot_rules = ia.load_rules()
    severity_counts = {"critical": 0, "major": 0, "minor": 0}
    issue_type_counts = {t.value: 0 for t in ia.IssueType}
    total = 0
    for im in (img1, img2):
        for iss in im.issues:
            total += 1
            severity_counts[iss.severity] += 1
            issue_type_counts[iss.type] += 1
    report = ia.AuditReport(
        site="amadey", started_at="2026-01-01T00:00:00",
        finished_at="2026-01-01T00:01:00", pages_crawled=2, images_total=2,
        issues_total=total, severity_counts=severity_counts,
        issue_type_counts=issue_type_counts, images=[img1, img2],
    )
    return report, bot_rules


def test_render_html_contains_counts():
    report, rules = _make_simple_report()
    html = iar.render_html(report, rules)
    assert "Image Audit Report" in html
    assert "amadey" in html
    assert str(report.images_total) in html
    assert "BROKEN_URL" in html


def test_write_report_creates_files(tmp_path):
    report, rules = _make_simple_report()
    out_dir = tmp_path / "out"
    index_path = iar.write_report(report, out_dir, rules=rules)
    assert index_path.exists()
    assert (out_dir / "assets" / "site.css").exists()
    content = index_path.read_text(encoding="utf-8")
    assert "Recommendations" in content


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def test_cli_run_json_exit_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ia.ImageAuditBot, "_load_robots", lambda self: None)

    page_resp = mock.Mock()
    page_resp.status_code = 200
    page_resp.text = DIVANINFO_HTML
    page_resp.content = SITEMAP_XML

    def fake_get(url, *a, **k):
        return page_resp

    monkeypatch.setattr(ia.requests.Session, "get", fake_get)
    monkeypatch.setattr(
        ia.ImageAuditBot, "discover_pages",
        lambda self: ["https://divaninfo.ru/catalog/divany/"],
    )
    monkeypatch.setattr(
        ia.ImageAuditBot, "head_image",
        lambda self, url, timeout=5: (200, "image/jpeg", 100 * 1024),
    )
    db_path = tmp_path / "cli.db"
    monkeypatch.setattr(ia, "DB_PATH", db_path)
    monkeypatch.setattr(ia, "REPORTS_DIR", tmp_path / "reports")

    rc = ia.main([
        "run", "--site", "divaninfo", "--max-pages", "5",
        "--delay", "0", "--json", "--no-report",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["site"] == "divaninfo"
    assert data["images_total"] == 4


def test_cli_report_no_runs_returns_one(tmp_path, monkeypatch):
    db_path = tmp_path / "empty.db"
    monkeypatch.setattr(ia, "DB_PATH", db_path)
    rc = ia.main(["report", "--site", "amadey", "--last",
                  "--out", str(tmp_path / "r.html")])
    assert rc == 1


def test_cli_report_from_stored_run(tmp_path, monkeypatch):
    _bot, _report, db_path = _run_bot(tmp_path, monkeypatch)
    monkeypatch.setattr(ia, "DB_PATH", db_path)
    out_file = tmp_path / "manual.html"
    rc = ia.main(["report", "--site", "amadey", "--last", "--out", str(out_file)])
    assert rc == 0
    assert out_file.exists()


# --------------------------------------------------------------------------
# config loading
# --------------------------------------------------------------------------
def test_load_site_config_unknown_raises():
    with pytest.raises(KeyError):
        ia.load_site_config("nope")


def test_load_site_config_has_audit_regex():
    cfg = ia.load_site_config("amadey")
    assert "audit_url_regex" in cfg
    assert cfg["user_agent"] == "OpenClawImageAuditBot/1.0"


def test_load_rules_defaults_merge():
    rules = ia.load_rules()
    assert rules["thresholds"]["png_max_kb"] == 200
    assert rules["severity"]["BROKEN_URL"] == "critical"
