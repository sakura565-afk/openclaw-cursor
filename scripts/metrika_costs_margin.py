#!/usr/bin/env python3
"""Daily Yandex Metrika report enriched with cost/margin data.

Extends the fetch pattern from ``scripts/metrika_daily.py`` (stdlib
``urllib`` GET against ``stat/v1/data``, OAuth header, optional HTTP proxy)
to the full set of amadey.ru / divaninfo.ru counters and landing pages, then
cross-references recent sales against the furniture cost database to report
margin by item and by category.

Env vars:
    YANDEX_METRIKA_TOKEN or YANDEX_METRIKA_OAUTH_TOKEN
        Metrika API OAuth token. Without it, the Metrika section is
        reported as unavailable but the script keeps running.
    METRIKA_HTTP_PROXY / HTTPS_PROXY
        Optional proxy for the Metrika API (default http://127.0.0.1:3067,
        same default as metrika_daily.py; disable with --no-proxy).
    TELEGRAM_MEDIA_SEND_SCRIPT
        Path to telegram_media_send_v2.py (default: the OpenClaw skill
        path used elsewhere in this repo, see DEFAULT_TELEGRAM_SCRIPT).
    TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
        Builtin-sender fallback (scripts/telegram_sender.py) when the
        external telegram_media_send_v2.py script is not present.

Inputs (all optional; missing files degrade the corresponding report
section instead of failing the run):
    data/costs/costs_<date>.json   -- cost database, schema costs-v1.4
    scripts/parsed_sales_v20.json  -- sales records for margin cross-check

Usage:
    python scripts/metrika_costs_margin.py 2026-09-02
    python scripts/metrika_costs_margin.py 2026-09-02 --skip-telegram
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# --------------------------------------------------------------------------- #
# Metrika counters (id, display label)
# --------------------------------------------------------------------------- #

COUNTERS: tuple[tuple[int, str], ...] = (
    (63403, "amadey.ru"),
    (94834593, "divaninfo.ru"),
    (110795180, "AMADEY | Распродажа"),
    (110992850, "DIVANINFO | Arizona"),
    (111303025, "DIVANINFO | Бескаркасные диваны"),
    (111529130, "DIVANINFO | Кровати и матрасы"),
    (111602665, "DIVANINFO | Матрасы Лендинг"),
)

METRIKA_API_BASE = "https://api-metrika.yandex.net"
STAT_METRICS = (
    "ym:s:visits",
    "ym:s:pageviews",
    "ym:s:avgVisitDurationSeconds",
)

DEFAULT_CHAT_ID = "25979298"
DEFAULT_TELEGRAM_SCRIPT = Path(
    r"C:\Users\user\.openclaw\skills\telegram-media-send\scripts\telegram_media_send_v2.py"
)

DEFAULT_COSTS_DIR = REPO_ROOT / "data" / "costs"
DEFAULT_SALES_FILE = REPO_ROOT / "scripts" / "parsed_sales_v20.json"

VAT_MULTIPLIER = 1.20
SALES_LOOKBACK_DAYS = 30

# Cyrillic look-alikes normalized to their Latin counterpart so a SKU typed
# with either alphabet still matches (constraint: "normalize Cyrillic").
_CYRILLIC_TO_LATIN = str.maketrans(
    {
        "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H",
        "О": "O", "Р": "P", "С": "C", "Т": "T", "У": "Y", "Х": "X",
        "а": "A", "в": "B", "е": "E", "к": "K", "м": "M", "н": "H",
        "о": "O", "р": "P", "с": "C", "т": "T", "у": "Y", "х": "X",
    }
)

_QUOTE_CHARS = "\"'«»“”‘’`"
_STANDARD_SUFFIXES = ("-STANDART", "-STANDARD", "-STD", " STANDART", " STANDARD", " STD")


# --------------------------------------------------------------------------- #
# Metrika fetch (same request shape as scripts/metrika_daily.py)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DayStats:
    visits: float
    pageviews: float
    avg_duration_seconds: float

    @classmethod
    def from_totals(cls, values: list[float]) -> "DayStats":
        return cls(
            visits=values[0] if len(values) > 0 else 0.0,
            pageviews=values[1] if len(values) > 1 else 0.0,
            avg_duration_seconds=values[2] if len(values) > 2 else 0.0,
        )


def _metrika_token() -> str:
    return (
        os.environ.get("YANDEX_METRIKA_TOKEN", "").strip()
        or os.environ.get("YANDEX_METRIKA_OAUTH_TOKEN", "").strip()
    )


def _default_proxy_url() -> str | None:
    return os.environ.get("METRIKA_HTTP_PROXY", "http://127.0.0.1:3067")


def _build_opener(proxy_url: str | None) -> urllib.request.OpenerDirector:
    if not proxy_url:
        return urllib.request.build_opener()
    handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
    return urllib.request.build_opener(handler)


def fetch_day_stats(
    oauth_token: str,
    counter_id: int,
    day: date,
    *,
    proxy_url: str | None,
    timeout: int = 60,
) -> DayStats:
    """GET /stat/v1/data for a single calendar day (accuracy=full)."""
    params = {
        "ids": counter_id,
        "date1": day.isoformat(),
        "date2": day.isoformat(),
        "metrics": ",".join(STAT_METRICS),
        "accuracy": "full",
    }
    query = urllib.parse.urlencode(params)
    url = f"{METRIKA_API_BASE}/stat/v1/data?{query}"
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"OAuth {oauth_token}",
            "Accept": "application/json",
        },
        method="GET",
    )
    opener = _build_opener(proxy_url)
    with opener.open(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))

    # API may return totals as a flat list [v0, v1, v2] (single-day or week group)
    # OR as a nested list [[d0], [d1], ...] (multi-day with day group). Handle both.
    totals = payload.get("totals", [])
    row = totals[0] if totals and isinstance(totals[0], list) else totals
    return DayStats.from_totals([float(x) for x in row])


def format_duration(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    s = int(round(seconds))
    m, sec = divmod(s, 60)
    return f"{m:d}:{sec:02d}"


def collect_metrika_rows(
    report_day: date, *, proxy_url: str | None
) -> list[tuple[int, str, DayStats | None, str | None]]:
    """Return (counter_id, label, stats_or_None, error_or_None) per counter."""
    token = _metrika_token()
    rows: list[tuple[int, str, DayStats | None, str | None]] = []
    if not token:
        for counter_id, label in COUNTERS:
            rows.append((counter_id, label, None, "YANDEX_METRIKA_TOKEN not set"))
        return rows

    for counter_id, label in COUNTERS:
        try:
            stats = fetch_day_stats(token, counter_id, report_day, proxy_url=proxy_url)
            rows.append((counter_id, label, stats, None))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            rows.append((counter_id, label, None, f"HTTP {exc.code}: {body[:200]}"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            rows.append((counter_id, label, None, str(exc)))
    return rows


# --------------------------------------------------------------------------- #
# Cost database + sales loading (graceful on missing/invalid files)
# --------------------------------------------------------------------------- #


def load_costs_db(report_day: date, costs_dir: Path = DEFAULT_COSTS_DIR) -> tuple[list[dict], Path | None, str | None]:
    """Load the costs-v1.4 item list. Returns (items, path_used, error)."""
    if not costs_dir.is_dir():
        return [], None, f"costs directory not found: {costs_dir}"

    exact = costs_dir / f"costs_{report_day.isoformat()}.json"
    candidates = [exact] if exact.is_file() else sorted(costs_dir.glob("costs_*.json"), reverse=True)
    if not candidates:
        return [], None, f"no costs_*.json files found in {costs_dir}"

    path = candidates[0]
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        return [], None, f"failed to read {path}: {exc}"

    items = data.get("items", data) if isinstance(data, dict) else data
    if not isinstance(items, list):
        return [], path, f"unexpected schema in {path}: expected a list of items"
    return items, path, None


def load_sales(sales_file: Path = DEFAULT_SALES_FILE) -> tuple[list[dict], str | None]:
    """Load parsed sales records. Returns (records, error)."""
    if not sales_file.is_file():
        return [], f"sales file not found: {sales_file}"
    try:
        with sales_file.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        return [], f"failed to read {sales_file}: {exc}"

    records = data.get("records", data) if isinstance(data, dict) else data
    if not isinstance(records, list):
        return [], f"unexpected schema in {sales_file}: expected a list of records"
    return records, None


def filter_recent_sales(records: Iterable[dict], report_day: date, days: int = SALES_LOOKBACK_DAYS) -> list[dict]:
    """Keep records whose `period` falls within the lookback window.

    `period` is matched loosely (YYYY-MM-DD or YYYY-MM); records with an
    unparseable or missing period are kept, since dropping unknown-date
    sales would silently understate revenue.
    """
    cutoff = report_day - timedelta(days=days)
    kept: list[dict] = []
    for rec in records:
        period = str(rec.get("period", "")).strip()
        parsed: date | None = None
        for fmt in ("%Y-%m-%d", "%Y-%m"):
            try:
                parsed = datetime.strptime(period, fmt).date()
                break
            except ValueError:
                continue
        if parsed is None or (cutoff <= parsed <= report_day):
            kept.append(rec)
    return kept


# --------------------------------------------------------------------------- #
# SKU normalization + matching
# --------------------------------------------------------------------------- #


def normalize_sku(raw: str | None) -> str:
    """Uppercase, strip quotes, normalize Cyrillic look-alikes, drop the
    standard-fabric suffix so SKUs from different sources compare equal."""
    if not raw:
        return ""
    text = str(raw).strip()
    for ch in _QUOTE_CHARS:
        text = text.replace(ch, "")
    text = text.strip()
    text = text.upper()
    text = text.translate(_CYRILLIC_TO_LATIN)
    for suffix in _STANDARD_SUFFIXES:
        if text.endswith(suffix.upper()):
            text = text[: -len(suffix)].strip()
            break
    return " ".join(text.split())


def article_key(sku: str | None) -> str:
    """First alnum token of a normalized SKU, used as a looser fallback key."""
    norm = normalize_sku(sku)
    token = norm.split("-")[0].split(" ")[0] if norm else ""
    return token


def build_cost_index(items: list[dict]) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    by_sku: dict[str, dict] = {}
    by_article: dict[str, list[dict]] = {}
    for item in items:
        sku_key = normalize_sku(item.get("sku"))
        if sku_key:
            by_sku.setdefault(sku_key, item)
        art = normalize_sku(item.get("article_key") or item.get("article")) or article_key(item.get("sku"))
        if art:
            by_article.setdefault(art, []).append(item)
    return by_sku, by_article


def match_sale_to_cost(sale: dict, by_sku: dict[str, dict], by_article: dict[str, list[dict]]) -> dict | None:
    sku_key = normalize_sku(sale.get("sku"))
    if sku_key and sku_key in by_sku:
        return by_sku[sku_key]

    art = article_key(sale.get("sku"))
    candidates = by_article.get(art, [])
    if not candidates:
        return None
    category = sale.get("category")
    if category:
        for candidate in candidates:
            if candidate.get("category") == category:
                return candidate
    return candidates[0]


def item_cost_with_vat(cost_item: dict, sale: dict) -> float | None:
    """Prefer fabric-specific cost (Классическая мебель) when a fabric is given."""
    fabric = sale.get("fabric")
    if fabric and cost_item.get("cost_by_fabric_with_vat"):
        by_fabric = cost_item["cost_by_fabric_with_vat"]
        if isinstance(by_fabric, dict) and fabric in by_fabric:
            return float(by_fabric[fabric])
    if cost_item.get("cost_with_vat") is not None:
        return float(cost_item["cost_with_vat"])
    if cost_item.get("cost") is not None:
        return float(cost_item["cost"]) * VAT_MULTIPLIER
    return None


def compute_margin(sale_total: float, cost_with_vat: float) -> tuple[float, float] | None:
    """Return (margin_rub, margin_pct) using revenue-ex-VAT minus cost-with-VAT."""
    revenue_ex_vat = sale_total / VAT_MULTIPLIER
    if revenue_ex_vat <= 0:
        return None
    margin_rub = revenue_ex_vat - cost_with_vat
    margin_pct = margin_rub / revenue_ex_vat * 100.0
    return margin_rub, margin_pct


@dataclass
class MatchedSale:
    sale: dict
    cost_item: dict
    cost_with_vat: float
    margin_rub: float
    margin_pct: float


def cross_check(sales: list[dict], items: list[dict]) -> tuple[list[MatchedSale], int]:
    by_sku, by_article = build_cost_index(items)
    matched: list[MatchedSale] = []
    for sale in sales:
        try:
            total = float(sale.get("total", 0) or 0)
        except (TypeError, ValueError):
            continue
        cost_item = match_sale_to_cost(sale, by_sku, by_article)
        if cost_item is None:
            continue
        cost_with_vat = item_cost_with_vat(cost_item, sale)
        if cost_with_vat is None:
            continue
        result = compute_margin(total, cost_with_vat)
        if result is None:
            continue
        margin_rub, margin_pct = result
        matched.append(MatchedSale(sale, cost_item, cost_with_vat, margin_rub, margin_pct))
    return matched, len(sales)


# --------------------------------------------------------------------------- #
# Report assembly
# --------------------------------------------------------------------------- #


def _fmt_rub(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ") + " руб"


def build_metrika_section(rows: list[tuple[int, str, DayStats | None, str | None]]) -> list[str]:
    lines: list[str] = []
    for counter_id, label, stats, error in rows:
        lines.append(f"### Counter: {label} ({counter_id})")
        if stats is None:
            lines.append(f"- Data unavailable: {error}")
        else:
            lines.append(f"- Visits: {stats.visits:,.0f}".replace(",", " "))
            lines.append(f"- Pageviews: {stats.pageviews:,.0f}".replace(",", " "))
            lines.append(f"- Avg time: {format_duration(stats.avg_duration_seconds)}")
        lines.append("")
    return lines


def build_revenue_section(sales: list[dict], sales_error: str | None) -> tuple[list[str], float, int]:
    lines = ["### Revenue (from parsed_sales_v20.json, last 30d):"]
    if sales_error and not sales:
        lines.append(f"- No sales data available ({sales_error})")
        lines.append("")
        return lines, 0.0, 0

    total = 0.0
    count = 0
    for sale in sales:
        try:
            total += float(sale.get("total", 0) or 0)
            count += 1
        except (TypeError, ValueError):
            continue

    if count == 0:
        lines.append("- No sales records in the lookback window")
        lines.append("")
        return lines, 0.0, 0

    avg_ticket = total / count
    lines.append(f"- Total sales: {_fmt_rub(total)} ({count} items)")
    lines.append(f"- Avg ticket: {_fmt_rub(avg_ticket)}")
    lines.append("")
    return lines, total, count


def build_cost_coverage_section(
    matched: list[MatchedSale],
    total_sales_count: int,
    costs_path: Path | None,
    costs_error: str | None,
) -> list[str]:
    label = costs_path.name if costs_path else "data/costs/costs_<date>.json"
    lines = [f"### Cost coverage (from {label}):"]

    if costs_error and costs_path is None:
        lines.append(f"- No cost data available ({costs_error})")
        lines.append("")
        return lines

    if total_sales_count == 0:
        lines.append("- No sales to cross-check against cost data")
        lines.append("")
        return lines

    coverage_pct = (len(matched) / total_sales_count * 100.0) if total_sales_count else 0.0
    lines.append(f"- Matched items: {len(matched)} / {total_sales_count} sales ({coverage_pct:.0f}% coverage)")

    if not matched:
        lines.append("- Avg margin: n/a (no matches)")
        lines.append("")
        return lines

    avg_margin = sum(m.margin_pct for m in matched) / len(matched)
    best = max(matched, key=lambda m: m.margin_pct)
    worst = min(matched, key=lambda m: m.margin_pct)
    lines.append(f"- Avg margin: {avg_margin:.1f}% (sales_total/1.2 - cost_with_vat basis)")
    lines.append(
        f"- Best margin item: {best.cost_item.get('name', best.sale.get('sku', '?'))} ({best.margin_pct:.1f}%)"
    )
    lines.append(
        f"- Worst margin item: {worst.cost_item.get('name', worst.sale.get('sku', '?'))} ({worst.margin_pct:.1f}%)"
    )
    lines.append("")
    return lines


def build_category_table(matched: list[MatchedSale]) -> list[str]:
    lines = [
        "### Top categories by margin (sorted desc):",
        "| Category | Sales | Cost | Margin % |",
        "|----------|-------|------|----------|",
    ]
    if not matched:
        lines.append("| (no matched sales) | - | - | - |")
        lines.append("")
        return lines

    by_category: dict[str, list[MatchedSale]] = {}
    for m in matched:
        category = m.cost_item.get("category") or m.sale.get("category") or "(unknown)"
        by_category.setdefault(category, []).append(m)

    rows = []
    for category, items in by_category.items():
        sales_total = sum(float(m.sale.get("total", 0) or 0) for m in items)
        cost_total = sum(m.cost_with_vat for m in items)
        revenue_ex_vat = sales_total / VAT_MULTIPLIER
        margin_pct = ((revenue_ex_vat - cost_total) / revenue_ex_vat * 100.0) if revenue_ex_vat else 0.0
        rows.append((category, sales_total, cost_total, margin_pct))

    rows.sort(key=lambda r: r[3], reverse=True)
    for category, sales_total, cost_total, margin_pct in rows:
        lines.append(f"| {category} | {_fmt_rub(sales_total)} | {_fmt_rub(cost_total)} | {margin_pct:.1f}% |")
    lines.append("")
    return lines


def build_report(
    report_day: date,
    metrika_rows: list[tuple[int, str, DayStats | None, str | None]],
    sales: list[dict],
    sales_error: str | None,
    items: list[dict],
    costs_path: Path | None,
    costs_error: str | None,
) -> str:
    lines: list[str] = [f"## Yandex Metrika + Costs — {report_day.isoformat()}", ""]
    lines.extend(build_metrika_section(metrika_rows))

    recent_sales = filter_recent_sales(sales, report_day) if sales else sales
    revenue_lines, _total, count = build_revenue_section(recent_sales, sales_error)
    lines.extend(revenue_lines)

    matched, total_sales_count = ([], count)
    if items and recent_sales:
        matched, total_sales_count = cross_check(recent_sales, items)

    lines.extend(build_cost_coverage_section(matched, total_sales_count, costs_path, costs_error))
    lines.extend(build_category_table(matched))
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# Telegram delivery (external telegram_media_send_v2.py, else builtin sender)
# --------------------------------------------------------------------------- #


def _resolve_telegram_script(explicit: Path | None) -> Path | None:
    if explicit is not None and explicit.is_file():
        return explicit
    env = os.environ.get("TELEGRAM_MEDIA_SEND_SCRIPT")
    if env:
        p = Path(env).expanduser()
        if p.is_file():
            return p
    return DEFAULT_TELEGRAM_SCRIPT if DEFAULT_TELEGRAM_SCRIPT.is_file() else None


def send_via_external_script(report_text: str, caption: str, script: Path, chat_id: str) -> dict:
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".txt", prefix="metrika_costs_margin_", delete=False
    ) as handle:
        handle.write(report_text)
        text_path = Path(handle.name)
    try:
        cmd = [sys.executable, str(script), str(text_path), "--caption", caption]
        env = os.environ.copy()
        env.setdefault("TELEGRAM_CHAT_ID", chat_id)
        env.setdefault("CHAT_ID", chat_id)
        completed = subprocess.run(cmd, capture_output=True, text=True, check=False, env=env, timeout=60)
        if completed.returncode == 0:
            return {"ok": True, "command": cmd}
        err = (completed.stderr or completed.stdout or "").strip() or f"exit {completed.returncode}"
        return {"ok": False, "error": err}
    finally:
        try:
            text_path.unlink(missing_ok=True)
        except OSError:
            pass


def send_via_builtin_sender(report_text: str, chat_id: str, *, proxy_url: str | None) -> dict:
    try:
        import requests

        from scripts.telegram_sender import TelegramConfig, TelegramSenderError
    except ImportError as exc:
        return {"ok": False, "error": f"telegram_sender unavailable: {exc}"}

    try:
        config = TelegramConfig.from_env()
    except TelegramSenderError as exc:
        return {"ok": False, "error": str(exc)}

    chat = config.chat_id or chat_id
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    url = f"https://api.telegram.org/bot{config.token}/sendMessage"
    # Telegram messages are capped at 4096 chars; truncate defensively.
    text = report_text if len(report_text) <= 4000 else report_text[:3990] + "\n…(truncated)"
    try:
        response = requests.post(
            url,
            data={"chat_id": chat, "text": text},
            proxies=proxies,
            timeout=config.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok", False):
            return {"ok": False, "error": payload.get("description", "sendMessage returned ok=false")}
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001 - report any transport failure, don't crash the run
        return {"ok": False, "error": str(exc)}


def deliver_to_telegram(
    report_text: str,
    report_day: date,
    *,
    chat_id: str,
    telegram_script: Path | None,
    proxy_url: str | None,
) -> dict:
    caption = f"Metrika + costs margin report {report_day.isoformat()}"
    script = _resolve_telegram_script(telegram_script)
    if script is not None:
        return send_via_external_script(report_text, caption, script, chat_id)
    return send_via_builtin_sender(report_text, chat_id, proxy_url=proxy_url)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _ensure_utf8_stdout() -> None:
    """Best-effort UTF-8 stdout so Cyrillic text is safe on cp1251 PowerShell."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    yesterday = date.today() - timedelta(days=1)
    parser = argparse.ArgumentParser(description="Yandex Metrika + cost/margin daily report.")
    parser.add_argument(
        "report_date",
        nargs="?",
        type=lambda s: date.fromisoformat(s),
        default=yesterday,
        help="Day to report (ISO date, e.g. 2026-09-02). Default: yesterday.",
    )
    parser.add_argument("--costs-dir", type=Path, default=DEFAULT_COSTS_DIR, help="Directory with costs_*.json.")
    parser.add_argument("--sales-file", type=Path, default=DEFAULT_SALES_FILE, help="Path to parsed sales JSON.")
    parser.add_argument("--skip-telegram", action="store_true", help="Only print the report.")
    parser.add_argument("--chat-id", default=os.environ.get("TELEGRAM_CHAT_ID", DEFAULT_CHAT_ID))
    parser.add_argument("--telegram-script", type=Path, default=None)
    parser.add_argument("--no-proxy", action="store_true", help="Disable METRIKA_HTTP_PROXY for Metrika/Telegram.")
    parser.add_argument("--output", type=Path, default=None, help="Optional path to also write the report text.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdout()
    args = parse_args(argv)
    report_day: date = args.report_date
    proxy_url = None if args.no_proxy else _default_proxy_url()

    metrika_rows = collect_metrika_rows(report_day, proxy_url=proxy_url)
    items, costs_path, costs_error = load_costs_db(report_day, args.costs_dir)
    sales, sales_error = load_sales(args.sales_file)

    report_text = build_report(
        report_day, metrika_rows, sales, sales_error, items, costs_path, costs_error
    )
    print(report_text)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report_text, encoding="utf-8")

    if not args.skip_telegram:
        result = deliver_to_telegram(
            report_text,
            report_day,
            chat_id=args.chat_id,
            telegram_script=args.telegram_script,
            proxy_url=proxy_url,
        )
        if result.get("ok"):
            print("Telegram: sent", file=sys.stderr)
        else:
            print(f"Telegram: skipped/failed — {result.get('error')}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
