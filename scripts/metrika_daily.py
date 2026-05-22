#!/usr/bin/env python3
"""
Daily Yandex Metrika HTML/PDF report for fixed counters, with day-over-day deltas
and Telegram delivery (builtin sender or optional telegram_media_send_v2-style CLI).

OAuth token: set YANDEX_METRIKA_OAUTH_TOKEN (see project TOOLS.md if your team keeps secrets there).

Proxy default: http://127.0.0.1:3067 (override with HTTPS_PROXY / METRIKA_HTTP_PROXY).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

# Repo root for imports when run as python scripts/metrika_daily.py
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

DEFAULT_COUNTERS: tuple[tuple[int, str], ...] = (
    (94834593, "amadey.ru"),
    (63403, "divaninfo.ru"),
)

METRIKA_API_BASE = "https://api-metrika.yandex.net"
STAT_METRICS = (
    "ym:s:visits",
    "ym:s:pageviews",
    "ym:s:users",
    "ym:s:bounceRate",
    "ym:s:avgVisitDurationSeconds",
)


@dataclass(frozen=True)
class DayStats:
    visits: float
    pageviews: float
    users: float
    bounce_rate: float  # 0..1 from API
    avg_duration_seconds: float

    @classmethod
    def from_totals(cls, values: list[float]) -> "DayStats":
        return cls(
            visits=values[0] if len(values) > 0 else 0.0,
            pageviews=values[1] if len(values) > 1 else 0.0,
            users=values[2] if len(values) > 2 else 0.0,
            bounce_rate=values[3] if len(values) > 3 else 0.0,
            avg_duration_seconds=values[4] if len(values) > 4 else 0.0,
        )


def _default_proxy_url() -> str:
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
    try:
        with opener.open(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Metrika HTTP {exc.code}: {body}") from exc

    totals = payload.get("totals", [[]])
    row = totals[0] if totals and isinstance(totals[0], list) else []
    floats = [float(x) for x in row]
    return DayStats.from_totals(floats)


def pct_delta(prev: float, curr: float) -> str:
    if prev == 0:
        return "—" if curr == 0 else "+∞"
    delta = (curr - prev) / prev * 100.0
    sign = "+" if delta > 0 else ""
    return f"{sign}{delta:.1f}%"


def format_duration(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h:d}:{m:02d}:{sec:02d}"
    return f"{m:d}:{sec:02d}"


def format_bounce(rate: float) -> str:
    # API returns 0..1
    if rate > 1.0 + 1e-6:
        pct = rate
    else:
        pct = rate * 100.0
    return f"{pct:.1f}%"


def build_html(
    report_day: date,
    rows: Iterable[tuple[str, int, DayStats, DayStats]],
) -> str:
    prev = report_day - timedelta(days=1)
    parts: list[str] = [
        "<!DOCTYPE html>",
        "<html><head><meta charset='utf-8'>",
        "<title>Yandex Metrika — daily report</title>",
        "<style>",
        "body{font-family:system-ui,Segoe UI,sans-serif;margin:24px;background:#fafafa;color:#222;}",
        "h1{font-size:1.4rem;}",
        "table{border-collapse:collapse;width:100%;max-width:900px;background:#fff;box-shadow:0 1px 3px #0001;}",
        "th,td{border:1px solid #ddd;padding:8px 10px;text-align:left;}",
        "th{background:#f0f4f8;}",
        ".num{text-align:right;font-variant-numeric:tabular-nums;}",
        ".up{color:#0a7a2f;}.down{color:#b00020;}",
        "caption{text-align:left;font-weight:600;margin-bottom:8px;}",
        "</style></head><body>",
        f"<h1>Yandex Metrika — {report_day.isoformat()}</h1>",
        f"<p>Compared to previous day ({prev.isoformat()}).</p>",
        "<table>",
        "<caption>Summary by site</caption>",
        "<thead><tr>",
        "<th>Site</th><th>Counter</th>",
        "<th class='num'>Visits</th><th class='num'>Δ prev</th>",
        "<th class='num'>Pageviews</th><th class='num'>Δ prev</th>",
        "<th class='num'>Users</th><th class='num'>Δ prev</th>",
        "<th class='num'>Bounce</th><th class='num'>Δ prev (pp)</th>",
        "<th class='num'>Avg duration</th><th class='num'>Δ prev</th>",
        "</tr></thead><tbody>",
    ]
    for label, cid, cur, prev_stats in rows:
        def cls_for_delta(curr_v: float, prev_v: float, *, lower_is_better: bool) -> str:
            if curr_v == prev_v:
                return ""
            better = curr_v < prev_v if lower_is_better else curr_v > prev_v
            return "up" if better else "down"

        dv = pct_delta(prev_stats.visits, cur.visits)
        dpv = pct_delta(prev_stats.pageviews, cur.pageviews)
        du = pct_delta(prev_stats.users, cur.users)
        bounce_pp = (cur.bounce_rate - prev_stats.bounce_rate) * 100
        if cur.bounce_rate > 1:
            bounce_pp = cur.bounce_rate - prev_stats.bounce_rate
        bounce_delta = f"{bounce_pp:+.1f} pp" if prev_stats.bounce_rate or cur.bounce_rate else "—"
        dur_delta = pct_delta(prev_stats.avg_duration_seconds, cur.avg_duration_seconds)

        parts.append("<tr>")
        parts.extend(
            [
                f"<td>{label}</td>",
                f"<td>{cid}</td>",
                f"<td class='num'>{cur.visits:,.0f}</td>",
                f"<td class='num {cls_for_delta(cur.visits, prev_stats.visits, lower_is_better=False)}'>{dv}</td>",
                f"<td class='num'>{cur.pageviews:,.0f}</td>",
                f"<td class='num {cls_for_delta(cur.pageviews, prev_stats.pageviews, lower_is_better=False)}'>{dpv}</td>",
                f"<td class='num'>{cur.users:,.0f}</td>",
                f"<td class='num {cls_for_delta(cur.users, prev_stats.users, lower_is_better=False)}'>{du}</td>",
                f"<td class='num'>{format_bounce(cur.bounce_rate)}</td>",
                f"<td class='num {cls_for_delta(cur.bounce_rate, prev_stats.bounce_rate, lower_is_better=True)}'>{bounce_delta}</td>",
                f"<td class='num'>{format_duration(cur.avg_duration_seconds)}</td>",
                f"<td class='num {cls_for_delta(cur.avg_duration_seconds, prev_stats.avg_duration_seconds, lower_is_better=False)}'>{dur_delta}</td>",
            ]
        )
        parts.append("</tr>")
    parts.append("</tbody></table></body></html>")
    return "\n".join(parts)


def write_pdf_report(
    report_day: date,
    rows: Iterable[tuple[str, int, DayStats, DayStats]],
    pdf_path: Path,
) -> None:
    """Render the same metrics as the HTML report using matplotlib (no extra system libs)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    prev_day = report_day - timedelta(days=1)
    headers = [
        "Site",
        "ID",
        "Visits",
        "ΔV",
        "PV",
        "ΔPV",
        "Users",
        "ΔU",
        "Bounce",
        "ΔBr",
        "Avg",
        "Δt",
    ]
    cell_data: list[list[str]] = []
    for label, cid, cur, prev_stats in rows:
        bounce_pp = (cur.bounce_rate - prev_stats.bounce_rate) * 100
        if cur.bounce_rate > 1:
            bounce_pp = cur.bounce_rate - prev_stats.bounce_rate
        bounce_delta = (
            f"{bounce_pp:+.1f}pp"
            if (prev_stats.bounce_rate or cur.bounce_rate)
            else "—"
        )
        cell_data.append(
            [
                label,
                str(cid),
                f"{cur.visits:,.0f}",
                pct_delta(prev_stats.visits, cur.visits),
                f"{cur.pageviews:,.0f}",
                pct_delta(prev_stats.pageviews, cur.pageviews),
                f"{cur.users:,.0f}",
                pct_delta(prev_stats.users, cur.users),
                format_bounce(cur.bounce_rate),
                bounce_delta,
                format_duration(cur.avg_duration_seconds),
                pct_delta(prev_stats.avg_duration_seconds, cur.avg_duration_seconds),
            ]
        )

    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    with PdfPages(pdf_path) as pdf:
        fig, ax = plt.subplots(figsize=(11, 8.5))
        ax.axis("off")
        fig.suptitle(
            f"Yandex Metrika — {report_day.isoformat()} (vs {prev_day.isoformat()})",
            fontsize=12,
            y=0.97,
        )
        table = ax.table(
            cellText=cell_data,
            colLabels=headers,
            cellLoc="center",
            loc="upper center",
        )
        table.auto_set_font_size(False)
        table.set_fontsize(7)
        table.scale(1, 1.85)
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)


def _resolve_telegram_script() -> Path | None:
    env = os.environ.get("TELEGRAM_MEDIA_SEND_SCRIPT")
    if env:
        p = Path(env).expanduser()
        return p if p.is_file() else None
    claw = Path.home() / ".openclaw" / "skills" / "telegram-media-send" / "scripts" / "telegram_media_send_v2.py"
    return claw if claw.is_file() else None


def send_telegram_document(
    pdf_path: Path,
    caption: str,
    *,
    external_script: Path | None,
    proxy_url: str | None,
) -> None:
    import requests

    from scripts.telegram_sender import TelegramConfig, TelegramSender

    if external_script is not None:
        cmd = [
            sys.executable,
            str(external_script),
            "send-document",
            str(pdf_path),
            "--caption",
            caption,
        ]
        env = os.environ.copy()
        if proxy_url:
            env.setdefault("HTTP_PROXY", proxy_url)
            env.setdefault("HTTPS_PROXY", proxy_url)
        subprocess.run(cmd, check=True, env=env, cwd=str(_REPO_ROOT))
        return

    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None

    def request_with_proxy(**kwargs: Any) -> requests.Response:
        kwargs.setdefault("proxies", proxies)
        return requests.request(**kwargs)

    config = TelegramConfig.from_env()
    sender = TelegramSender(config, request_func=request_with_proxy)
    sender.send_document(pdf_path, caption=caption)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    yesterday = date.today() - timedelta(days=1)
    p = argparse.ArgumentParser(description="Yandex Metrika daily HTML/PDF report + Telegram.")
    p.add_argument(
        "--report-date",
        type=lambda s: date.fromisoformat(s),
        default=yesterday,
        help="Day to report (ISO date). Default: yesterday (local).",
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=_REPO_ROOT / "reports" / "metrika_daily",
        help="Directory for HTML/PDF output.",
    )
    p.add_argument("--skip-telegram", action="store_true", help="Only write files.")
    p.add_argument(
        "--no-proxy",
        action="store_true",
        help="Do not use METRIKA_HTTP_PROXY / default 127.0.0.1:3067 for Metrika or Telegram.",
    )
    p.add_argument(
        "--telegram-script",
        type=Path,
        default=None,
        help="Path to telegram_media_send_v2.py (default: env TELEGRAM_MEDIA_SEND_SCRIPT or ~/.openclaw/...).",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    token = os.environ.get("YANDEX_METRIKA_OAUTH_TOKEN", "").strip()
    if not token:
        print(
            "Missing YANDEX_METRIKA_OAUTH_TOKEN in environment.",
            file=sys.stderr,
        )
        return 2

    proxy_url = None if args.no_proxy else _default_proxy_url()

    report_day: date = args.report_date
    prev_day = report_day - timedelta(days=1)

    rows_out: list[tuple[str, int, DayStats, DayStats]] = []
    try:
        for counter_id, label in DEFAULT_COUNTERS:
            cur = fetch_day_stats(token, counter_id, report_day, proxy_url=proxy_url)
            prev_stats = fetch_day_stats(token, counter_id, prev_day, proxy_url=proxy_url)
            rows_out.append((label, counter_id, cur, prev_stats))
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1

    html = build_html(report_day, rows_out)
    out_dir: Path = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"metrika_daily_{report_day.isoformat()}"
    html_path = out_dir / f"{stem}.html"
    pdf_path = out_dir / f"{stem}.pdf"
    html_path.write_text(html, encoding="utf-8")
    write_pdf_report(report_day, rows_out, pdf_path)

    caption = f"Metrika daily {report_day.isoformat()} (vs {prev_day.isoformat()})"
    if not args.skip_telegram:
        ext = args.telegram_script
        if ext is not None and ext.is_file():
            script_path: Path | None = ext
        else:
            script_path = _resolve_telegram_script()
        send_telegram_document(pdf_path, caption, external_script=script_path, proxy_url=proxy_url)

    print(f"Wrote {html_path} and {pdf_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
