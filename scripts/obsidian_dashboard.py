#!/usr/bin/env python3
"""
Obsidian Vault Dashboard

Flask dashboard for monitoring Obsidian vault stats:
- Overall file statistics
- Project-level breakdown
- Recent changes
- Broken wiki-link checker
- Unlinked mentions
- Tag frequency analysis
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import frontmatter
import markdown2
from flask import Flask, jsonify, render_template

WIKI_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
TAG_RE = re.compile(r"(?<!\w)#([A-Za-z0-9][\w/-]*)")
DEFAULT_VAULT_PATH = r"C:\Users\user\Documents\Obsidian Vault"
LOG_PATH = Path("memory/obsidian_dashboard_log.md")


def bytes_to_human(size: int) -> str:
    """Convert bytes to human-readable value."""
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{size} B"


def now_iso() -> str:
    """Current timestamp in ISO format."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log_event(title: str, details: str = "") -> None:
    """Append markdown log entry to memory/obsidian_dashboard_log.md."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = [f"## {now_iso()} — {title}"]
    if details:
        entry.append(details.strip())
    entry.append("")
    with LOG_PATH.open("a", encoding="utf-8") as file:
        file.write("\n".join(entry))


def normalize_note_name(path: Path) -> str:
    """Normalize note path to Obsidian note key without extension."""
    return path.with_suffix("").as_posix()


def parse_wiki_target(raw_target: str) -> str:
    """
    Parse wiki link target:
      [[path/to/note|alias]] -> path/to/note
      [[note#header]] -> note
    """
    target = raw_target.split("|", 1)[0].strip()
    target = target.split("#", 1)[0].strip()
    return target


def extract_wiki_links(content: str) -> list[str]:
    """Extract raw wiki-link targets from markdown content."""
    links: list[str] = []
    for match in WIKI_LINK_RE.findall(content):
        target = parse_wiki_target(match)
        if target:
            links.append(target)
    return links


def extract_tags(content: str, frontmatter_tags: Any) -> list[str]:
    """Extract hashtags from content + tags from frontmatter."""
    tags = list(TAG_RE.findall(content))
    if isinstance(frontmatter_tags, str):
        tags.append(frontmatter_tags.strip("# "))
    elif isinstance(frontmatter_tags, list):
        for item in frontmatter_tags:
            if isinstance(item, str):
                tags.append(item.strip("# "))
    return [tag for tag in tags if tag]


def get_project_stats(project_dir: str | Path) -> dict[str, Any]:
    """
    Return project statistics:
    {
      "project": "...",
      "file_count": int,
      "total_size": int,
      "total_size_human": str,
      "last_modified": "...",
      "types": {"md": 5, "png": 2}
    }
    """
    root = Path(project_dir)
    file_count = 0
    total_size = 0
    extension_counter: Counter[str] = Counter()
    latest_mtime = 0.0

    if not root.exists() or not root.is_dir():
        return {
            "project": root.name,
            "file_count": 0,
            "total_size": 0,
            "total_size_human": "0.00 B",
            "last_modified": "N/A",
            "types": {},
        }

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        file_count += 1
        total_size += stat.st_size
        latest_mtime = max(latest_mtime, stat.st_mtime)
        ext = path.suffix.lower().lstrip(".") or "no_ext"
        extension_counter[ext] += 1

    last_modified = (
        datetime.fromtimestamp(latest_mtime).strftime("%Y-%m-%d %H:%M:%S")
        if latest_mtime
        else "N/A"
    )
    return {
        "project": root.name,
        "file_count": file_count,
        "total_size": total_size,
        "total_size_human": bytes_to_human(total_size),
        "last_modified": last_modified,
        "types": dict(sorted(extension_counter.items())),
    }


def collect_markdown_notes(vault_root: Path) -> dict[str, Path]:
    """Build an index of markdown notes by possible wiki-link keys."""
    notes: dict[str, Path] = {}
    for md_path in vault_root.rglob("*.md"):
        rel = md_path.relative_to(vault_root)
        key_full = normalize_note_name(rel)
        key_basename = rel.stem
        notes[key_full.lower()] = md_path
        notes[key_basename.lower()] = md_path
    return notes


def find_broken_links(vault_path: str | Path) -> list[dict[str, str]]:
    """
    Find broken internal wiki links for markdown files.
    Returns list of: {"source": "...", "target": "..."}
    """
    vault_root = Path(vault_path).expanduser()
    notes_index = collect_markdown_notes(vault_root)
    broken: list[dict[str, str]] = []

    for md_path in vault_root.rglob("*.md"):
        try:
            content = md_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        for target in extract_wiki_links(content):
            target_key = target.lower().strip("/")
            if target_key and target_key not in notes_index:
                broken.append(
                    {
                        "source": md_path.relative_to(vault_root).as_posix(),
                        "target": target,
                    }
                )
    return broken


def scan_vault(vault_path: str | Path) -> dict[str, Any]:
    """
    Scan Obsidian vault and return dashboard data dictionary.
    """
    vault_root = Path(vault_path).expanduser()
    if not vault_root.exists():
        raise FileNotFoundError(f"Vault path does not exist: {vault_root}")
    if not vault_root.is_dir():
        raise NotADirectoryError(f"Vault path is not a directory: {vault_root}")

    extension_counter: Counter[str] = Counter()
    total_files = 0
    total_size = 0
    recent_changes: list[dict[str, Any]] = []
    tag_counter: Counter[str] = Counter()
    mentions_without_links: list[dict[str, str]] = []
    link_usage: dict[str, set[str]] = defaultdict(set)

    markdown_files: list[Path] = []
    all_files: list[Path] = []
    notes_index = collect_markdown_notes(vault_root)
    note_titles = {path.stem: path for path in notes_index.values()}

    for path in vault_root.rglob("*"):
        if not path.is_file():
            continue
        all_files.append(path)
        total_files += 1
        try:
            stat = path.stat()
        except OSError:
            continue
        total_size += stat.st_size
        ext = path.suffix.lower().lstrip(".") or "no_ext"
        extension_counter[ext] += 1
        recent_changes.append(
            {
                "path": path.relative_to(vault_root).as_posix(),
                "size": stat.st_size,
                "size_human": bytes_to_human(stat.st_size),
                "modified_ts": stat.st_mtime,
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
            }
        )
        if path.suffix.lower() == ".md":
            markdown_files.append(path)

    for md_path in markdown_files:
        rel_source = md_path.relative_to(vault_root).as_posix()
        try:
            post = frontmatter.load(md_path)
            content = post.content or ""
            fm_tags = post.metadata.get("tags", [])
        except Exception:
            content = md_path.read_text(encoding="utf-8", errors="ignore")
            fm_tags = []

        links = extract_wiki_links(content)
        for target in links:
            link_usage[rel_source].add(target.lower())

        for tag in extract_tags(content, fm_tags):
            tag_counter[tag.lower()] += 1

        # Unlinked mention: note title appears in text but not as wiki link.
        lower_content = content.lower()
        for title in note_titles.keys():
            if title.lower() == md_path.stem.lower():
                continue
            if title.lower() in lower_content and title.lower() not in link_usage[rel_source]:
                if f"[[{title.lower()}]]" not in lower_content:
                    mentions_without_links.append(
                        {"source": rel_source, "mentioned_note": title}
                    )

    recent_changes.sort(key=lambda item: item["modified_ts"], reverse=True)
    recent_changes = recent_changes[:50]

    projects_dir = vault_root / "01_Projects"
    project_stats: list[dict[str, Any]] = []
    if projects_dir.exists() and projects_dir.is_dir():
        for child in sorted(projects_dir.iterdir()):
            if child.is_dir():
                project_stats.append(get_project_stats(child))

    broken_links = find_broken_links(vault_root)
    unique_mentions = {(m["source"], m["mentioned_note"]): m for m in mentions_without_links}

    stats = {
        "vault_path": str(vault_root),
        "scanned_at": now_iso(),
        "summary": {
            "total_files": total_files,
            "total_size": total_size,
            "total_size_human": bytes_to_human(total_size),
            "file_types": dict(sorted(extension_counter.items(), key=lambda x: (-x[1], x[0]))),
            "markdown_files": len(markdown_files),
        },
        "project_breakdown": project_stats,
        "recent_changes": recent_changes,
        "broken_links": broken_links,
        "unlinked_mentions": list(unique_mentions.values()),
        "tags_frequency": dict(tag_counter.most_common(100)),
        "top_files_by_size": sorted(
            [
                {
                    "path": path.relative_to(vault_root).as_posix(),
                    "size": path.stat().st_size if path.exists() else 0,
                }
                for path in all_files
            ],
            key=lambda item: item["size"],
            reverse=True,
        )[:20],
    }
    log_event(
        "Vault scan completed",
        f"- Path: `{vault_root}`\n"
        f"- Files: {total_files}\n"
        f"- Broken links: {len(broken_links)}\n"
        f"- Unlinked mentions: {len(unique_mentions)}",
    )
    return stats


def generate_report(vault_stats: dict[str, Any], output_format: str = "markdown") -> str:
    """Generate dashboard report in markdown or html."""
    summary = vault_stats["summary"]
    markdown_lines = [
        "# Obsidian Vault Dashboard Report",
        "",
        f"- Scanned at: **{vault_stats['scanned_at']}**",
        f"- Vault path: `{vault_stats['vault_path']}`",
        f"- Total files: **{summary['total_files']}**",
        f"- Total size: **{summary['total_size_human']}**",
        f"- Markdown files: **{summary['markdown_files']}**",
        "",
        "## File types",
    ]
    for ext, count in summary["file_types"].items():
        markdown_lines.append(f"- `{ext}`: {count}")

    markdown_lines.extend(["", "## Project breakdown"])
    for project in vault_stats["project_breakdown"]:
        markdown_lines.append(
            f"- **{project['project']}**: {project['file_count']} files, "
            f"{project['total_size_human']}, last update {project['last_modified']}"
        )

    markdown_lines.extend(["", f"## Broken links ({len(vault_stats['broken_links'])})"])
    for item in vault_stats["broken_links"][:50]:
        markdown_lines.append(f"- `{item['source']}` -> `[[{item['target']}]]`")

    markdown_lines.extend(["", "## Top tags"])
    for tag, count in list(vault_stats["tags_frequency"].items())[:25]:
        markdown_lines.append(f"- `#{tag}`: {count}")

    report_md = "\n".join(markdown_lines)
    if output_format.lower() == "html":
        return markdown2.markdown(report_md)
    return report_md


def create_app(vault_path: str) -> Flask:
    """Create and configure Flask app."""
    app = Flask(__name__, template_folder="../templates")

    def load_stats() -> dict[str, Any]:
        try:
            return scan_vault(vault_path)
        except Exception as exc:
            log_event("Scan failed", f"- Error: `{exc}`")
            return {
                "vault_path": vault_path,
                "scanned_at": now_iso(),
                "summary": {
                    "total_files": 0,
                    "total_size": 0,
                    "total_size_human": "0.00 B",
                    "file_types": {},
                    "markdown_files": 0,
                },
                "project_breakdown": [],
                "recent_changes": [],
                "broken_links": [{"source": "system", "target": str(exc)}],
                "unlinked_mentions": [],
                "tags_frequency": {},
                "top_files_by_size": [],
                "error": str(exc),
            }

    @app.route("/")
    def dashboard() -> str:
        stats = load_stats()
        return render_template("dashboard.html", stats=stats)

    @app.route("/api/stats")
    def api_stats():
        return jsonify(load_stats())

    @app.route("/report.md")
    def report_md() -> str:
        stats = load_stats()
        return generate_report(stats, output_format="markdown"), 200, {"Content-Type": "text/markdown; charset=utf-8"}

    @app.route("/report.html")
    def report_html() -> str:
        stats = load_stats()
        html = generate_report(stats, output_format="html")
        return html, 200, {"Content-Type": "text/html; charset=utf-8"}

    return app


def run_server(vault_path: str = DEFAULT_VAULT_PATH, port: int = 5000) -> None:
    """Run Flask dashboard server."""
    log_event("Server starting", f"- Vault: `{vault_path}`\n- Port: {port}")
    app = create_app(vault_path=vault_path)
    app.run(host="0.0.0.0", port=port, debug=False)


def parse_args() -> argparse.Namespace:
    """CLI args."""
    parser = argparse.ArgumentParser(description="Obsidian Vault Dashboard")
    parser.add_argument(
        "--vault-path",
        default=os.getenv("OBSIDIAN_VAULT_PATH", DEFAULT_VAULT_PATH),
        help="Path to Obsidian vault",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("OBSIDIAN_DASHBOARD_PORT", "5000")),
        help="Port for dashboard web server",
    )
    parser.add_argument(
        "--dump-json",
        action="store_true",
        help="Scan vault and print JSON stats (without starting server)",
    )
    parser.add_argument(
        "--report-format",
        choices=["markdown", "html"],
        default="markdown",
        help="Report format for --report output",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Generate report and print to stdout (without starting server)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.dump_json:
        stats = scan_vault(args.vault_path)
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return
    if args.report:
        stats = scan_vault(args.vault_path)
        print(generate_report(stats, output_format=args.report_format))
        return
    run_server(vault_path=args.vault_path, port=args.port)


if __name__ == "__main__":
    main()
