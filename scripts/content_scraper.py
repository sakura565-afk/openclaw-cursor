#!/usr/bin/env python3
"""Async fetchers for public Reddit JSON listings and the 4chan catalog/threads (`scripts.config.FOURCHAN_BOARD_SLUG`).
Uses only unauthenticated endpoints. Many networks receive HTTP 403 from Reddit; scrape from an allowed network if needed."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import html
import json
import logging
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

try:
    import aiohttp  # noqa: TRY003 — optional error message guides install
except ImportError:
    aiohttp = None  # pragma: no cover

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts import config as cfg


log = logging.getLogger(__name__)

SourceName = str  # ``reddit`` | ``fourchan``


@dataclass(frozen=True)
class ContentRecord:
    source: SourceName
    url: str
    timestamp: str  # ISO-8601 UTC
    raw_text: str
    slang_found: list[str]
    category: str

    def content_hash(self) -> str:
        normalized = "\n".join(self.raw_text.strip().lower().split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _utc_iso(unix_ts: float | int | None) -> str:
    if unix_ts is None:
        ts = datetime.now(timezone.utc)
    else:
        ts = datetime.fromtimestamp(float(unix_ts), tz=timezone.utc)
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def _strip_markup(text: str) -> str:
    t = html.unescape(text or "")
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def passes_toxicity_filter(text_lower: str) -> bool:
    return not any(bad in text_lower for bad in cfg.TOXICITY_BLOCK_SUBSTRINGS)


def find_slang(raw_lower: str) -> list[str]:
    hits: list[str] = []
    for seed in cfg.all_slang_seeds():
        s = seed.lower()
        if s and s in raw_lower:
            hits.append(seed)
    return sorted(set(hits), key=len, reverse=True)


def load_dedupe_set(processed_dir: Path) -> set[str]:
    path = processed_dir / cfg.DEDUPE_INDEX_NAME
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        hashes = data.get("hashes")
        if isinstance(hashes, list):
            return {str(h) for h in hashes}
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Could not load dedupe index %s: %s", path, exc)
    return set()


def save_dedupe_set(processed_dir: Path, known: set[str]) -> None:
    processed_dir.mkdir(parents=True, exist_ok=True)
    path = processed_dir / cfg.DEDUPE_INDEX_NAME
    path.write_text(
        json.dumps({"hashes": sorted(known)}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _record_to_json(rec: ContentRecord) -> dict[str, Any]:
    return {
        "source": rec.source,
        "url": rec.url,
        "timestamp": rec.timestamp,
        "raw_text": rec.raw_text,
        "slang_found": rec.slang_found,
        "category": rec.category,
    }


async def _sleep_rl(delay_sec: float) -> None:
    await asyncio.sleep(delay_sec)


async def reddit_fetch(
    session: aiohttp.ClientSession,
    hours: float,
    raw_dir: Path,
    processed_dir: Path,
    known_hashes: set[str],
) -> dict[str, int]:
    """Reddit ingest with clearer counters."""
    now = time.time()
    cutoff = now - float(hours) * 3600.0
    stats = {"fetched_posts": 0, "saved_raw_new": 0, "saved_processed_new": 0, "skipped_duplicate_raw": 0, "skipped_toxic_or_dup_proc": 0}

    for sub in cfg.REDDIT_SUBREDDITS:
        sub_slug = sub.removeprefix("r/").strip().lower()
        query = urlencode({"limit": str(cfg.REDDIT_PER_REQUEST_LIMIT), "raw_json": "1"})
        payload: dict[str, Any] | None = None
        for base in (cfg.REDDIT_BASE.rstrip("/"), "https://old.reddit.com"):
            url = f"{base}/r/{sub_slug}/{cfg.REDDIT_LISTING}.json?{query}"
            try:
                async with session.get(url, allow_redirects=True) as resp:
                    if resp.status == 403:
                        log.warning("Reddit %s HTTP 403 — trying next host if available", url)
                        continue
                    if resp.status != 200:
                        log.warning("Reddit %s HTTP %s", url, resp.status)
                        break
                    payload = await resp.json()
                    break
            except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as exc:
                log.warning("Reddit fetch failed %s: %s", url, exc)
                continue

        await _sleep_rl(cfg.REDDIT_RATE_LIMIT_DELAY_SEC)

        if payload is None:
            continue

        children = (((payload or {}).get("data") or {}).get("children")) or []
        for child in children:
            data = (child or {}).get("data") or {}
            created = data.get("created_utc")
            if created is not None and float(created) < cutoff:
                continue

            title = str(data.get("title") or "")
            body = str(data.get("selftext") or "")
            raw_text = "\n\n".join(p for p in (title, body) if p).strip()
            if not raw_text:
                url = str(data.get("url") or "").strip()
                if title and url:
                    raw_text = f"{title}\n{url}".strip()
            if not raw_text:
                continue

            stats["fetched_posts"] += 1
            permalink = str(data.get("permalink") or "")
            full_url = permalink if permalink.startswith("http") else f"{cfg.REDDIT_BASE}{permalink}"
            lower = raw_text.lower()
            slang = find_slang(lower)

            rec = ContentRecord(
                source="reddit",
                url=full_url,
                timestamp=_utc_iso(created),
                raw_text=raw_text,
                slang_found=slang,
                category=cfg.categorize_text(lower, bool(slang)),
            )

            h = rec.content_hash()
            raw_path = raw_dir / f"{h}.json"
            proc_path = processed_dir / f"{h}.json"
            raw_dir.mkdir(parents=True, exist_ok=True)

            if raw_path.is_file():
                stats["skipped_duplicate_raw"] += 1
            else:
                write_json_atomic(raw_path, _record_to_json(rec))
                stats["saved_raw_new"] += 1

            if not passes_toxicity_filter(lower):
                stats["skipped_toxic_or_dup_proc"] += 1
                continue

            if h in known_hashes or proc_path.is_file():
                stats["skipped_toxic_or_dup_proc"] += 1
                continue

            write_json_atomic(proc_path, _record_to_json(rec))
            known_hashes.add(h)
            stats["saved_processed_new"] += 1

    save_dedupe_set(processed_dir, known_hashes)
    return stats


async def fourchan_fetch(
    session: aiohttp.ClientSession,
    hours: float,
    raw_dir: Path,
    processed_dir: Path,
    known_hashes: set[str],
) -> dict[str, int]:
    """Catalog scan + bounded per-thread JSON fetches for ``FOURCHAN_BOARD_SLUG``."""
    now = time.time()
    cutoff = now - float(hours) * 3600.0
    stats = {
        "catalog_threads_seen": 0,
        "thread_json_fetches": 0,
        "posts_saved_raw_new": 0,
        "posts_saved_processed_new": 0,
        "skipped_off_window": 0,
        "skipped_duplicate_or_toxic": 0,
    }

    catalog_url = cfg.FOURCHAN_CATALOG_URL
    try:
        async with session.get(catalog_url) as resp:
            if resp.status != 200:
                log.warning("4chan catalog HTTP %s", resp.status)
                return stats
            catalog = await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as exc:
        log.warning("4chan catalog failed: %s", exc)
        return stats

    await _sleep_rl(cfg.FOURCHAN_RATE_LIMIT_DELAY_SEC)

    thread_ids: list[tuple[float, int]] = []
    for page in catalog or []:
        for th in page.get("threads") or []:
            try:
                tim = float(th.get("time") or 0)
                no = int(th.get("no") or 0)
            except (TypeError, ValueError):
                continue
            if tim >= cutoff:
                thread_ids.append((tim, no))
            stats["catalog_threads_seen"] += 1

    thread_ids.sort(key=lambda x: x[0], reverse=True)
    picked = sorted({no for _, no in thread_ids[: cfg.FOURCHAN_MAX_THREADS_PER_RUN]})

    async def ingest_post(*, unix_t: float, web_url: str, com_raw: str) -> None:
        raw_text = _strip_markup(com_raw)
        if not raw_text:
            return
        if unix_t < cutoff:
            stats["skipped_off_window"] += 1
            return

        lower = raw_text.lower()
        slang = find_slang(lower)
        rec = ContentRecord(
            source="fourchan",
            url=web_url,
            timestamp=_utc_iso(unix_t),
            raw_text=raw_text,
            slang_found=slang,
            category=cfg.categorize_text(lower, bool(slang)),
        )

        h = rec.content_hash()
        raw_path = raw_dir / f"{h}.json"
        proc_path = processed_dir / f"{h}.json"
        raw_dir.mkdir(parents=True, exist_ok=True)

        if raw_path.is_file():
            stats["skipped_duplicate_or_toxic"] += 1
            return

        write_json_atomic(raw_path, _record_to_json(rec))
        stats["posts_saved_raw_new"] += 1

        if not passes_toxicity_filter(lower):
            stats["skipped_duplicate_or_toxic"] += 1
            return
        if h in known_hashes or proc_path.is_file():
            stats["skipped_duplicate_or_toxic"] += 1
            return

        write_json_atomic(proc_path, _record_to_json(rec))
        known_hashes.add(h)
        stats["posts_saved_processed_new"] += 1

    for no in picked:
        t_url = cfg.FOURCHAN_THREAD_URL_TEMPLATE.format(no=no)
        try:
            async with session.get(t_url) as resp:
                stats["thread_json_fetches"] += 1
                if resp.status != 200:
                    log.warning("4chan thread %s HTTP %s", no, resp.status)
                    continue
                th_json = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as exc:
            log.warning("4chan thread %s failed: %s", no, exc)
            continue

        await _sleep_rl(cfg.FOURCHAN_RATE_LIMIT_DELAY_SEC)

        posts = (th_json or {}).get("posts") or []
        for p in posts:
            try:
                pid = int(p.get("no") or 0)
                tim = float(p.get("time") or 0)
            except (TypeError, ValueError):
                continue
            com = str(p.get("com") or "")
            web_u = f"{cfg.FOURCHAN_THREAD_WEB_BASE}/{no}#{pid}"
            await ingest_post(unix_t=tim, web_url=web_u, com_raw=com)

    save_dedupe_set(processed_dir, known_hashes)
    return stats


def aggregate_stats(repo: Path) -> dict[str, Any]:
    raw_dir = repo / cfg.DATA_RAW_DIR
    proc_dir = repo / cfg.DATA_PROCESSED_DIR

    def count_json(d: Path) -> int:
        if not d.is_dir():
            return 0
        return sum(1 for p in d.glob("*.json") if p.name != cfg.DEDUPE_INDEX_NAME)

    dedupe_n = len(load_dedupe_set(proc_dir))
    sources: dict[str, int] = {}
    if proc_dir.is_dir():
        for path in sorted(proc_dir.glob("*.json")):
            if path.name == cfg.DEDUPE_INDEX_NAME:
                continue
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            src = str(row.get("source") or "unknown")
            sources[src] = sources.get(src, 0) + 1

    return {
        "raw_json_files": count_json(raw_dir),
        "processed_json_files": count_json(proc_dir),
        "processed_dedupe_index_size": dedupe_n,
        "sources_processed_counts": dict(sorted(sources.items(), key=lambda kv: kv[0])),
    }


def cmd_scrape(repo: Path, source: str, hours: float) -> dict[str, Any]:
    timeout = aiohttp.ClientTimeout(total=cfg.HTTP_TIMEOUT_TOTAL_SEC)
    raw_dir = repo / cfg.DATA_RAW_DIR
    proc_dir = repo / cfg.DATA_PROCESSED_DIR
    known = load_dedupe_set(proc_dir)

    headers = {"User-Agent": cfg.HTTP_USER_AGENT}

    async def runner() -> dict[str, Any]:
        summary: dict[str, Any] = {"hours": hours, "sources": {}}
        connector = aiohttp.TCPConnector(limit_per_host=4)
        async with aiohttp.ClientSession(
            timeout=timeout, headers=headers, connector=connector
        ) as session:
            if source in ("reddit", "all"):
                summary["sources"]["reddit"] = await reddit_fetch(session, hours, raw_dir, proc_dir, known)
            if source in ("fourchan", "all"):
                summary["sources"]["fourchan"] = await fourchan_fetch(session, hours, raw_dir, proc_dir, known)
        return summary

    return asyncio.run(runner())


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Public content scraper (Reddit JSON + 4chan board JSON).")
    sub = p.add_subparsers(dest="cmd", required=True)

    scrape = sub.add_parser("scrape", help="Fetch posts from configured public sources.")
    scrape.add_argument(
        "--source",
        choices=("reddit", "fourchan", "all"),
        default="all",
        help="Which source to scrape (default: all unless --all is passed).",
    )
    scrape.add_argument(
        "--all",
        action="store_true",
        help="Scrape every configured source (same effect as --source all).",
    )
    scrape.add_argument(
        "--hours",
        type=float,
        default=24.0,
        help="Only keep items with timestamps within this window (default: 24).",
    )

    sub.add_parser("stats", help="Print JSON summary of data/raw and data/processed.")

    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if aiohttp is None:
        log.error("Install aiohttp: pip install aiohttp")
        return 2

    args = build_parser().parse_args(argv)

    repo = repo_root()
    if args.cmd == "stats":
        payload = aggregate_stats(repo)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "scrape":
        source = "all" if args.all else args.source
        payload = cmd_scrape(repo, source, args.hours)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
