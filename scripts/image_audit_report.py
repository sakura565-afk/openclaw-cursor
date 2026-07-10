#!/usr/bin/env python3
"""HTML report generator for the image audit tool.

Renders the Jinja2 template ``config/templates/image_audit_report.html.j2`` for
an :class:`~image_audit.AuditReport` and writes ``index.html`` plus a small
``assets/site.css`` into the target directory.

The report contains:
  * a header with site name, run timestamp and totals by severity,
  * a site-wide issue-type breakdown,
  * the top 20 worst offending pages,
  * a sortable per-page table, and
  * rule-based recommendations sourced from ``config/image_audit_rules.yaml``.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = REPO_ROOT / "config" / "templates"
TEMPLATE_NAME = "image_audit_report.html.j2"

SITE_CSS = """/* Image audit report styling */
:root {
  --bg: #f5f6f8;
  --panel: #ffffff;
  --ink: #1f2430;
  --muted: #6b7280;
  --line: #e3e6ec;
  --critical: #c0392b;
  --major: #d97706;
  --minor: #2563eb;
  --accent: #0f766e;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
  background: var(--bg);
  color: var(--ink);
  line-height: 1.5;
}
.hero {
  background: linear-gradient(135deg, #0f766e, #155e75);
  color: #fff;
  padding: 2rem 1.5rem;
}
.hero h1 { margin: 0 0 .25rem; font-size: 1.8rem; }
.subtitle { margin: 0 0 1.25rem; opacity: .92; }
.cards { display: flex; flex-wrap: wrap; gap: .75rem; }
.card {
  background: rgba(255, 255, 255, .12);
  border-radius: 10px;
  padding: .75rem 1.1rem;
  min-width: 96px;
  display: flex;
  flex-direction: column;
}
.card .num { font-size: 1.6rem; font-weight: 700; }
.card .lbl { font-size: .8rem; text-transform: uppercase; letter-spacing: .04em; opacity: .85; }
.card.sev-critical { background: rgba(192, 57, 43, .85); }
.card.sev-major { background: rgba(217, 119, 6, .9); }
.card.sev-minor { background: rgba(37, 99, 235, .85); }
main { max-width: 1100px; margin: 0 auto; padding: 1.5rem; }
section { background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 1.25rem 1.5rem; margin-bottom: 1.5rem; }
h2 { margin-top: 0; font-size: 1.25rem; }
.hint { color: var(--muted); font-size: .85rem; margin-top: -.4rem; }
table.data { width: 100%; border-collapse: collapse; font-size: .9rem; }
table.data th, table.data td { text-align: left; padding: .5rem .6rem; border-bottom: 1px solid var(--line); vertical-align: top; }
table.data thead th { background: #f0f2f5; position: sticky; top: 0; }
table.data .num-col { text-align: right; white-space: nowrap; }
table.data code { background: #f0f2f5; padding: .1rem .35rem; border-radius: 4px; }
table.data a { color: var(--accent); word-break: break-all; }
.badge { display: inline-block; padding: .1rem .5rem; border-radius: 999px; font-size: .75rem; color: #fff; text-transform: uppercase; letter-spacing: .03em; }
.badge.sev-critical, .sev-critical .num { }
.badge.sev-critical { background: var(--critical); }
.badge.sev-major { background: var(--major); }
.badge.sev-minor { background: var(--minor); }
.reco-list { list-style: none; padding: 0; margin: 0; }
.reco-list li { padding: .55rem 0; border-bottom: 1px solid var(--line); }
.reco-list li:last-child { border-bottom: none; }
.reco-list a { margin-left: .35rem; color: var(--accent); }
.notes { color: var(--muted); font-size: .85rem; }
.notes h3 { color: var(--ink); }
.generated { margin-top: .75rem; font-style: italic; }
"""


def _severity_of(issue) -> str:
    return getattr(issue, "severity", None) or (
        issue.get("severity") if isinstance(issue, dict) else "minor"
    )


def _type_of(issue) -> str:
    return getattr(issue, "type", None) or (
        issue.get("type") if isinstance(issue, dict) else ""
    )


def _aggregate_pages(report) -> list[dict]:
    """Build per-page issue counts (images + issues by severity)."""
    pages: dict[str, dict] = {}
    for img in report.images:
        page = pages.setdefault(
            img.page_url,
            {"url": img.page_url, "images": 0, "issues": 0,
             "critical": 0, "major": 0, "minor": 0},
        )
        page["images"] += 1
        for issue in img.issues:
            page["issues"] += 1
            sev = _severity_of(issue)
            if sev in page:
                page[sev] += 1
    return sorted(pages.values(), key=lambda p: p["issues"], reverse=True)


def _issue_type_rows(report, rules: Optional[dict]) -> list[dict]:
    rules = rules or {}
    severity_map = rules.get("severity", {})
    recommendations = rules.get("recommendations", {})
    rows = []
    for issue_type, count in report.issue_type_counts.items():
        rec = recommendations.get(issue_type, {}) or {}
        rows.append(
            {
                "type": issue_type,
                "count": count,
                "severity": severity_map.get(issue_type, "minor"),
                "recommendation": (rec.get("text") or "").strip()
                or "Review this issue type and apply the standard fix.",
                "url": rec.get("url", ""),
            }
        )
    return sorted(rows, key=lambda r: r["count"], reverse=True)


def build_context(report, rules: Optional[dict] = None) -> dict:
    """Assemble the Jinja2 render context from an AuditReport."""
    per_page = _aggregate_pages(report)
    return {
        "report": report,
        "site_name": (rules or {}).get("site_name", report.site),
        "per_page": per_page,
        "worst_offenders": per_page[:20],
        "issue_type_rows": _issue_type_rows(report, rules),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def render_html(report, rules: Optional[dict] = None) -> str:
    """Render the report HTML string (no files written)."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "xml", "j2"]),
    )
    template = env.get_template(TEMPLATE_NAME)
    return template.render(**build_context(report, rules))


def write_report(
    report,
    out_dir: Path,
    rules: Optional[dict] = None,
    index_name: str = "index.html",
) -> Path:
    """Render and write ``index.html`` + ``assets/site.css`` under ``out_dir``.

    Returns the path to the written HTML file.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = out_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    (assets_dir / "site.css").write_text(SITE_CSS, encoding="utf-8")

    html = render_html(report, rules)
    index_path = out_dir / index_name
    index_path.write_text(html, encoding="utf-8")
    return index_path
