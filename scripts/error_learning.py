#!/usr/bin/env python3
"""Capture and learn from recurring OpenClaw session errors.

Features include persisted deduplication fingerprints, cluster tagging for similar
errors, log watching, pattern analysis, and time-bucketed statistics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG_PATH = ROOT / ".learnings" / "error_log.json"
SCHEMA_VERSION = 1
DEDUP_CACHE_SCHEMA_VERSION = 1

# Ratios at or above this treat two error messages as the same "cluster" for tagging.
SIMILARITY_RATIO_THRESHOLD = 0.82

ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "cyan": "\033[36m",
}
FALLBACK_CATEGORY_COLORS = ("red", "yellow", "green")


class ErrorLearningError(RuntimeError):
    """Raised when the error learning log cannot be read or written."""


def colorize(text: str, color: str) -> str:
    """Wrap text in ANSI color codes unless the user disabled them."""

    if os.environ.get("NO_COLOR"):
        return text
    prefix = ANSI.get(color, "")
    if not prefix:
        return text
    return f"{prefix}{text}{ANSI['reset']}"


def normalize_text(text: str) -> str:
    """Normalize free-form text for comparisons and search."""

    return " ".join(text.strip().lower().split())


def dedup_cache_path(log_path: Path) -> Path:
    """Sidecar JSON path storing fingerprint timestamps for fast duplicate checks."""

    return log_path.parent / f"{log_path.name}.dedup_cache.json"


def category_color(category: str) -> str:
    """Choose a stable display color for a category name."""

    normalized = normalize_text(category)
    if any(token in normalized for token in ("lesson", "resolved", "fix", "success")):
        return "green"
    if any(token in normalized for token in ("warn", "warning", "caution")):
        return "yellow"
    if any(token in normalized for token in ("error", "failure", "fatal", "exception", "crash", "bug")):
        return "red"
    digest = hashlib.sha1(normalized.encode("utf-8")).digest()[0]
    return FALLBACK_CATEGORY_COLORS[digest % len(FALLBACK_CATEGORY_COLORS)]


def canonical_payload(category: str, error: str, lesson: str, resolved: bool) -> dict[str, object]:
    """Return a normalized payload used for IDs and deduplication."""

    return {
        "category": normalize_text(category),
        "error": normalize_text(error),
        "lesson": normalize_text(lesson),
        "resolved": bool(resolved),
    }


def stable_cluster_tag_from_error(category: str, error: str) -> str:
    """Deterministic tag when no peer cluster exists yet (suffix from category + error)."""

    payload = f"{normalize_text(category)}|{normalize_text(error)}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]
    return f"grp-{digest}"


def assign_cluster_tag_for_entry(
    peers: list[dict[str, object]],
    category: str,
    error: str,
) -> str:
    """Pick an existing cluster tag from similar peers, or allocate a new stable grp-* id."""

    norm_err = normalize_text(error)
    norm_cat = normalize_text(category)
    best_score = 0.0
    best_tag = ""

    for peer in peers:
        peer_err = normalize_text(str(peer["error"]))
        peer_cat = normalize_text(str(peer["category"]))
        err_ratio = SequenceMatcher(None, norm_err, peer_err).ratio()
        cat_ratio = SequenceMatcher(None, norm_cat, peer_cat).ratio()
        # Weight error text more heavily than category so wording ties clusters together.
        combined = err_ratio * 0.88 + cat_ratio * 0.12
        if err_ratio >= SIMILARITY_RATIO_THRESHOLD and combined > best_score:
            best_score = combined
            raw_tag = peer.get("cluster_tag")
            if isinstance(raw_tag, str) and raw_tag.strip():
                best_tag = raw_tag.strip()
            else:
                best_tag = stable_cluster_tag_from_error(str(peer["category"]), str(peer["error"]))

    if best_tag:
        return best_tag
    return stable_cluster_tag_from_error(category, error)


def build_entry(
    category: str,
    error: str,
    lesson: str,
    *,
    resolved: bool = True,
    timestamp: str | None = None,
    cluster_tag: str | None = None,
) -> dict[str, object]:
    """Create a log entry that matches the JSON schema."""

    payload = canonical_payload(category, error, lesson, resolved)
    digest = hashlib.sha1(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]
    created_at = timestamp or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    entry: dict[str, object] = {
        "id": digest,
        "timestamp": created_at,
        "category": category.strip(),
        "error": error.strip(),
        "lesson": lesson.strip(),
        "resolved": bool(resolved),
    }
    if cluster_tag is not None:
        entry["cluster_tag"] = cluster_tag.strip()
    return entry


def default_store() -> dict[str, object]:
    """Return an empty log document."""

    return {"schema_version": SCHEMA_VERSION, "entries": []}


def default_dedup_cache() -> dict[str, object]:
    """Return an empty deduplication sidecar document."""

    return {"schema_version": DEDUP_CACHE_SCHEMA_VERSION, "fingerprints": {}}


def load_dedup_cache(cache_path: Path) -> dict[str, object]:
    """Load fingerprint timestamps from disk (falls back to an empty cache)."""

    if not cache_path.exists():
        return default_dedup_cache()

    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ErrorLearningError(f"Unable to parse dedup cache JSON from {cache_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ErrorLearningError("Dedup cache must contain a JSON object.")

    fps = raw.get("fingerprints", {})
    if fps is not None and not isinstance(fps, dict):
        raise ErrorLearningError("Dedup cache field 'fingerprints' must be a JSON object.")

    cleaned: dict[str, object] = {}
    for key, value in (fps or {}).items():
        if not isinstance(key, str) or not key.strip():
            continue
        if isinstance(value, dict):
            last_seen = value.get("last_seen")
            first_seen = value.get("first_seen")
            occurrences = value.get("occurrences", 1)
            if isinstance(last_seen, str) and isinstance(first_seen, str):
                dup_ct = value.get("duplicate_suppressed_count")
                if dup_ct is None and isinstance(occurrences, (int, float)) and int(occurrences) > 1:
                    # Older caches overloaded "occurrences"; treat extras as suppressed repeats.
                    dup_ct = max(0, int(occurrences) - 1)
                dup_int = int(dup_ct) if isinstance(dup_ct, (int, float)) else 0
                meta: dict[str, object] = {
                    "last_seen": last_seen,
                    "first_seen": first_seen,
                    "duplicate_suppressed_count": max(0, dup_int),
                }
                last_dup = value.get("last_duplicate_attempt_at")
                if isinstance(last_dup, str) and last_dup.strip():
                    meta["last_duplicate_attempt_at"] = last_dup.strip()
                cleaned[key.strip()] = meta
        elif isinstance(value, str):
            # Legacy shape: fingerprint -> ISO timestamp string.
            cleaned[key.strip()] = {
                "last_seen": value,
                "first_seen": value,
                "duplicate_suppressed_count": 0,
            }

    return {"schema_version": int(raw.get("schema_version", DEDUP_CACHE_SCHEMA_VERSION)), "fingerprints": cleaned}


def save_dedup_cache(cache_path: Path, cache: dict[str, object]) -> None:
    """Persist deduplication fingerprints next to the main log."""

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=2) + "\n", encoding="utf-8")


def _min_iso_timestamp(a: str, b: str) -> str:
    """Return the earlier of two ISO-like timestamps (best-effort lexical fallback)."""

    da, db = parse_timestamp({"timestamp": a}), parse_timestamp({"timestamp": b})
    if da and db:
        return a if da <= db else b
    return a if a <= b else b


def _max_iso_timestamp(a: str, b: str) -> str:
    """Return the later of two ISO-like timestamps."""

    da, db = parse_timestamp({"timestamp": a}), parse_timestamp({"timestamp": b})
    if da and db:
        return a if da >= db else b
    return a if a >= b else b


def sync_dedup_cache_with_store(
    disk_cache: dict[str, object],
    entries: list[dict[str, object]],
) -> dict[str, object]:
    """Rebuild fingerprint rows from the JSON log while preserving duplicate-hit counters.

    The error log remains authoritative for which fingerprints exist; the sidecar file
    remembers how often ``add`` tried to re-insert the same learning across sessions.
    """

    validated = [validate_entry(e) for e in entries]
    disk_fps = disk_cache.get("fingerprints", {})
    assert isinstance(disk_fps, dict)

    fingerprints: dict[str, object] = {}
    for entry in validated:
        entry_id = str(entry["id"]).strip()
        ts = str(entry["timestamp"]).strip()
        prev = disk_fps.get(entry_id)
        dup_ct = 0
        old_first = ts
        old_last = ts
        last_attempt: str | None = None
        if isinstance(prev, dict):
            dup_ct = int(prev.get("duplicate_suppressed_count", 0))
            if isinstance(prev.get("first_seen"), str):
                old_first = _min_iso_timestamp(ts, str(prev["first_seen"]))
            if isinstance(prev.get("last_seen"), str):
                old_last = _max_iso_timestamp(ts, str(prev["last_seen"]))
            la = prev.get("last_duplicate_attempt_at")
            if isinstance(la, str) and la.strip():
                last_attempt = la.strip()

        row: dict[str, object] = {
            "first_seen": old_first,
            "last_seen": old_last,
            "duplicate_suppressed_count": max(0, dup_ct),
        }
        if last_attempt:
            row["last_duplicate_attempt_at"] = last_attempt
        fingerprints[entry_id] = row

    return {"schema_version": DEDUP_CACHE_SCHEMA_VERSION, "fingerprints": fingerprints}


def note_duplicate_suppressed(cache: dict[str, object], entry_id: str) -> None:
    """Increment suppressed duplicate attempts for a fingerprint already present in the store."""

    fingerprints = cache["fingerprints"]
    assert isinstance(fingerprints, dict)
    entry_id = entry_id.strip()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    meta = fingerprints.get(entry_id)
    if not isinstance(meta, dict):
        fingerprints[entry_id] = {
            "first_seen": now,
            "last_seen": now,
            "duplicate_suppressed_count": 1,
            "last_duplicate_attempt_at": now,
        }
        return

    meta["duplicate_suppressed_count"] = int(meta.get("duplicate_suppressed_count", 0)) + 1
    meta["last_duplicate_attempt_at"] = now


def validate_entry(raw_entry: object) -> dict[str, object]:
    """Validate a single persisted entry and normalize minor omissions."""

    if not isinstance(raw_entry, dict):
        raise ErrorLearningError("Each entry in the error log must be a JSON object.")

    entry = dict(raw_entry)
    required_text_fields = ("timestamp", "category", "error", "lesson")
    for field in required_text_fields:
        value = entry.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ErrorLearningError(f"Entry field '{field}' must be a non-empty string.")

    resolved = entry.get("resolved", False)
    if not isinstance(resolved, bool):
        raise ErrorLearningError("Entry field 'resolved' must be a boolean.")

    if not isinstance(entry.get("id"), str) or not entry["id"].strip():
        rebuilt = build_entry(
            entry["category"],
            entry["error"],
            entry["lesson"],
            resolved=resolved,
            timestamp=entry["timestamp"],
            cluster_tag=str(entry["cluster_tag"]) if isinstance(entry.get("cluster_tag"), str) else None,
        )
        entry["id"] = rebuilt["id"]

    cluster_raw = entry.get("cluster_tag")
    if cluster_raw is not None:
        if not isinstance(cluster_raw, str) or not cluster_raw.strip():
            entry.pop("cluster_tag", None)
        else:
            entry["cluster_tag"] = cluster_raw.strip()

    entry["resolved"] = resolved
    return entry


def load_store(log_path: Path) -> dict[str, object]:
    """Load the persisted error log from disk."""

    if not log_path.exists():
        return default_store()

    try:
        raw = json.loads(log_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ErrorLearningError(f"Unable to parse JSON from {log_path}: {exc}") from exc

    if isinstance(raw, list):
        entries = [validate_entry(item) for item in raw]
        return {"schema_version": SCHEMA_VERSION, "entries": entries}

    if not isinstance(raw, dict):
        raise ErrorLearningError("Error log must contain a JSON object or list of entries.")

    raw_entries = raw.get("entries", [])
    if not isinstance(raw_entries, list):
        raise ErrorLearningError("Error log field 'entries' must be a JSON array.")

    return {
        "schema_version": int(raw.get("schema_version", SCHEMA_VERSION)),
        "entries": [validate_entry(item) for item in raw_entries],
    }


def save_store(log_path: Path, store: dict[str, object]) -> None:
    """Persist the error log to disk."""

    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(store, indent=2) + "\n", encoding="utf-8")


def entries_match(left: dict[str, object], right: dict[str, object]) -> bool:
    """Return True when two entries are the same learning."""

    if left.get("id") == right.get("id"):
        return True
    return canonical_payload(
        str(left["category"]),
        str(left["error"]),
        str(left["lesson"]),
        bool(left["resolved"]),
    ) == canonical_payload(
        str(right["category"]),
        str(right["error"]),
        str(right["lesson"]),
        bool(right["resolved"]),
    )


def find_duplicate_entry(
    entries: list[dict[str, object]],
    candidate: dict[str, object],
    *,
    dedup_cache: dict[str, object],
) -> dict[str, object] | None:
    """Locate an existing entry matching candidate using cache-backed ids first."""

    fingerprints = dedup_cache.get("fingerprints", {})
    assert isinstance(fingerprints, dict)

    cand_id = str(candidate["id"]).strip()
    if cand_id in fingerprints:
        for entry in entries:
            if str(entry["id"]).strip() == cand_id:
                return validate_entry(entry)
        fingerprints.pop(cand_id, None)

    for entry in entries:
        validated = validate_entry(entry)
        if entries_match(validated, candidate):
            return validated

    return None


def add_entry(
    log_path: Path,
    category: str,
    error: str,
    lesson: str,
    *,
    resolved: bool = True,
) -> tuple[dict[str, object], bool]:
    """Add an error learning entry unless it already exists.

    Duplicate detection uses a file-backed fingerprint cache plus a full scan
    fallback so manual edits to the JSON log remain respected.
    """

    cache_path = dedup_cache_path(log_path)
    disk_cache = load_dedup_cache(cache_path)
    store = load_store(log_path)
    entries = store["entries"]
    assert isinstance(entries, list)

    cache = sync_dedup_cache_with_store(disk_cache, entries)

    new_entry = build_entry(category, error, lesson, resolved=resolved)
    cluster_tag = assign_cluster_tag_for_entry(entries, category, error)
    new_entry["cluster_tag"] = cluster_tag

    duplicate = find_duplicate_entry(entries, new_entry, dedup_cache=cache)
    if duplicate is not None:
        note_duplicate_suppressed(cache, str(duplicate["id"]))
        save_dedup_cache(cache_path, cache)
        return duplicate, False

    entries.append(new_entry)
    entries.sort(key=lambda item: str(item["timestamp"]), reverse=True)
    save_store(log_path, store)
    cache = sync_dedup_cache_with_store(cache, entries)
    save_dedup_cache(cache_path, cache)
    return new_entry, True


def parse_timestamp(entry: dict[str, object]) -> datetime | None:
    """Parse entry timestamps into UTC datetimes for analytics."""

    raw = str(entry.get("timestamp", "")).strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def format_entry(entry: dict[str, object]) -> str:
    """Render a single entry for console output."""

    category = str(entry["category"])
    resolved = bool(entry["resolved"])
    status_text = "resolved" if resolved else "open"
    status_color = "green" if resolved else "yellow"
    cluster = entry.get("cluster_tag")
    cluster_line = ""
    if isinstance(cluster, str) and cluster.strip():
        cluster_line = f"  {colorize('Cluster:', 'cyan')} {cluster.strip()}"

    lines = [
        (
            f"{colorize(category, category_color(category))} "
            f"{colorize(f'[{status_text}]', status_color)} "
            f"{colorize(str(entry['timestamp']), 'cyan')}"
        ),
        f"  {colorize('ID:', 'yellow')} {entry['id']}",
    ]
    if cluster_line:
        lines.append(cluster_line)
    lines.extend(
        [
            f"  {colorize('Error:', 'red')} {entry['error']}",
            f"  {colorize('Lesson:', 'green')} {entry['lesson']}",
        ]
    )
    return "\n".join(lines)


def print_entries(entries: list[dict[str, object]], *, heading: str) -> None:
    """Print a collection of entries in a human-readable layout."""

    print(colorize(heading, "bold"))
    print(colorize("=" * len(heading), "cyan"))
    if not entries:
        print(colorize("No entries found.", "yellow"))
        return

    for index, entry in enumerate(entries):
        if index:
            print()
        print(format_entry(entry))


def weekly_bucket(dt: datetime) -> str:
    """ISO year-week label used for coarse trends."""

    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def compute_weekly_trends(entries: list[dict[str, object]]) -> list[tuple[str, int]]:
    """Count entries per ISO week (sorted chronologically)."""

    counts: Counter[str] = Counter()
    for entry in entries:
        dt = parse_timestamp(entry)
        if dt is None:
            continue
        counts[weekly_bucket(dt)] += 1

    return sorted(counts.items(), key=lambda item: item[0])


def print_trends_section(entries: list[dict[str, object]]) -> None:
    """Print simple week-over-week volume trends."""

    trends = compute_weekly_trends(entries)
    print()
    print(colorize("Trends (entries per ISO week)", "bold"))
    print(colorize("------------------------------", "cyan"))
    if not trends:
        print(colorize("Not enough dated entries for weekly buckets.", "yellow"))
        return

    max_count = max(count for _, count in trends)
    bar_width = 28
    for label, count in trends[-12:]:
        share = count / max_count if max_count else 0
        filled = int(share * bar_width)
        bar = "#" * filled + "-" * (bar_width - filled)
        print(f"  {label}  {colorize(str(count), 'red'):>4}  {bar}")


def print_stats(entries: list[dict[str, object]]) -> None:
    """Print category-level frequency stats and resolution breakdown."""

    print(colorize("Error Learning Stats", "bold"))
    print(colorize("====================", "cyan"))
    if not entries:
        print(colorize("No entries found.", "yellow"))
        return

    counts = Counter(str(entry["category"]) for entry in entries)
    total = len(entries)
    resolved_n = sum(1 for entry in entries if bool(entry["resolved"]))
    open_n = total - resolved_n
    print(
        f"- {colorize('Total entries', 'bold')}: {colorize(str(total), 'cyan')} "
        f"({colorize(str(resolved_n), 'green')} resolved, {colorize(str(open_n), 'yellow')} open)"
    )
    print()
    for category, count in sorted(counts.items(), key=lambda item: (-item[1], item[0].lower())):
        share = (count / total) * 100
        print(
            f"- {colorize(category, category_color(category))}: "
            f"{colorize(str(count), 'red')} "
            f"({share:.1f}%)"
        )

    print_trends_section(entries)


def search_score(query: str, entry: dict[str, object]) -> float:
    """Score how relevant an entry is to a search query."""

    normalized_query = normalize_text(query)
    haystack = normalize_text(
        " ".join((str(entry["category"]), str(entry["error"]), str(entry["lesson"])))
    )
    if not normalized_query:
        return 0.0

    substring_bonus = 1.5 if normalized_query in haystack else 0.0
    query_tokens = set(normalized_query.split())
    haystack_tokens = set(haystack.split())
    overlap = len(query_tokens & haystack_tokens) / max(len(query_tokens), 1)
    ratio = SequenceMatcher(None, normalized_query, haystack).ratio()
    return substring_bonus + overlap + (ratio * 0.5)


def search_entries(entries: list[dict[str, object]], query: str, limit: int = 10) -> list[dict[str, object]]:
    """Return the most relevant matching entries for the given query."""

    ranked: list[tuple[float, dict[str, object]]] = []
    for entry in entries:
        score = search_score(query, entry)
        if score >= 0.45:
            ranked.append((score, entry))
    ranked.sort(key=lambda item: (-item[0], str(item[1]["timestamp"])), reverse=False)
    return [entry for _, entry in ranked[:limit]]


def analyze_patterns(entries: list[dict[str, object]]) -> None:
    """Summarize clusters, categories, and repeated failure wording."""

    print(colorize("Error Pattern Analysis", "bold"))
    print(colorize("======================", "cyan"))
    if not entries:
        print(colorize("No entries found.", "yellow"))
        return

    validated = [validate_entry(e) for e in entries]

    # Ensure every row carries a cluster tag for reporting (in-memory only).
    tag_by_entry_id: dict[str, str] = {}
    for entry in sorted(validated, key=lambda e: str(e["timestamp"])):
        eid = str(entry["id"])
        existing = entry.get("cluster_tag")
        if isinstance(existing, str) and existing.strip():
            tag_by_entry_id[eid] = existing.strip()
            continue
        peers = [pe for pe in validated if str(pe["id"]) != eid]
        tag_by_entry_id[eid] = assign_cluster_tag_for_entry(
            peers,
            str(entry["category"]),
            str(entry["error"]),
        )

    clusters: dict[str, list[dict[str, object]]] = defaultdict(list)
    for entry in validated:
        tid = str(entry["id"])
        clusters[tag_by_entry_id[tid]].append(entry)

    print()
    print(colorize("Clusters (similar errors)", "bold"))
    multi = [(tag, items) for tag, items in clusters.items() if len(items) > 1]
    multi.sort(key=lambda item: (-len(item[1]), item[0]))
    if not multi:
        print(colorize("No multi-entry clusters yet — add more similar errors to see groupings.", "yellow"))
    else:
        for tag, items in multi[:25]:
            cats = Counter(str(it["category"]) for it in items)
            top_cat = cats.most_common(1)[0][0]
            resolved_share = sum(1 for it in items if bool(it["resolved"])) / len(items) * 100
            sample_err = str(sorted(items, key=lambda it: str(it["timestamp"]))[-1]["error"])
            preview = sample_err if len(sample_err) <= 120 else sample_err[:117] + "..."
            print(
                f"- {colorize(tag, 'cyan')}  "
                f"{colorize(str(len(items)), 'red')} hits  "
                f"top category {colorize(top_cat, category_color(top_cat))}  "
                f"{resolved_share:.0f}% resolved"
            )
            print(f"    Latest wording: {preview}")

    print()
    print(colorize("Category resolution rates", "bold"))
    by_cat: dict[str, list[dict[str, object]]] = defaultdict(list)
    for entry in validated:
        by_cat[str(entry["category"])].append(entry)
    for cat in sorted(by_cat.keys(), key=lambda c: (-len(by_cat[c]), c.lower())):
        bucket = by_cat[cat]
        res = sum(1 for it in bucket if bool(it["resolved"]))
        pct = res / len(bucket) * 100 if bucket else 0.0
        print(
            f"- {colorize(cat, category_color(cat))}: "
            f"{res}/{len(bucket)} resolved ({pct:.0f}%)"
        )

    print_trends_section(validated)


def watch_log(
    log_path: Path,
    *,
    poll_interval: float,
    state_path: Path | None,
) -> None:
    """Poll the JSON log and print new entries as they land."""

    state_file = state_path or (log_path.parent / f"{log_path.name}.watch_state.json")
    seen_ids: set[str] = set()
    if state_file.exists():
        try:
            raw = json.loads(state_file.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("seen_ids"), list):
                seen_ids = {str(x) for x in raw["seen_ids"] if isinstance(x, str)}
        except json.JSONDecodeError:
            seen_ids = set()

    print(
        colorize(
            f"Watching {log_path} (poll every {poll_interval:.1f}s, Ctrl+C to stop)",
            "bold",
        )
    )

    last_mtime: float | None = None
    try:
        while True:
            try:
                mtime = log_path.stat().st_mtime if log_path.exists() else None
            except OSError:
                mtime = None

            if mtime != last_mtime and log_path.exists():
                last_mtime = mtime
                try:
                    store = load_store(log_path)
                except ErrorLearningError as exc:
                    print(colorize(str(exc), "red"), file=sys.stderr)
                    time.sleep(poll_interval)
                    continue

                raw_entries = store["entries"]
                assert isinstance(raw_entries, list)
                entries_chrono = sorted(raw_entries, key=lambda e: str(e["timestamp"]))
                new_entries = [e for e in entries_chrono if str(e["id"]) not in seen_ids]

                for entry in new_entries:
                    print()
                    print(colorize(f"--- New entry @ {datetime.now(timezone.utc).isoformat(timespec='seconds')}Z ---", "cyan"))
                    print(format_entry(validate_entry(entry)))
                    seen_ids.add(str(entry["id"]))

                state_file.parent.mkdir(parents=True, exist_ok=True)
                payload = {
                    "schema_version": 1,
                    "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                    "seen_ids": sorted(seen_ids),
                }
                state_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

            time.sleep(max(0.1, poll_interval))
    except KeyboardInterrupt:
        print(colorize("\nStopped watch.", "yellow"))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""

    parser = argparse.ArgumentParser(description="Capture and learn from OpenClaw errors.")
    parser.add_argument(
        "--log-path",
        type=Path,
        default=DEFAULT_LOG_PATH,
        help="Path to the error learning JSON log.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="Add a new error learning entry.")
    add_parser.add_argument("error_category", help="High-level category for the error.")
    add_parser.add_argument("error_message", help="Error message or failure summary.")
    add_parser.add_argument("lesson_learned", help="Lesson learned from the failure.")
    resolved_group = add_parser.add_mutually_exclusive_group()
    resolved_group.add_argument(
        "--resolved",
        dest="resolved",
        action="store_true",
        default=True,
        help="Mark the entry as resolved (default).",
    )
    resolved_group.add_argument(
        "--unresolved",
        dest="resolved",
        action="store_false",
        help="Mark the entry as still open.",
    )

    subparsers.add_parser("list", help="List all learned errors.")
    subparsers.add_parser("stats", help="Show error frequency, resolution mix, and weekly trends.")

    search_parser = subparsers.add_parser("search", help="Search for relevant past errors.")
    search_parser.add_argument("query", help="Search query.")
    search_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of matching entries to print.",
    )

    watch_parser = subparsers.add_parser("watch", help="Tail new log entries as the JSON file grows.")
    watch_parser.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="Polling interval in seconds (default: 2).",
    )
    watch_parser.add_argument(
        "--state-path",
        type=Path,
        default=None,
        help="Optional JSON file tracking seen entry ids (defaults next to the log).",
    )

    subparsers.add_parser("analyze", help="Print cluster and category pattern summaries.")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the error learning CLI."""

    args = parse_args(argv)
    try:
        store = load_store(args.log_path)
    except ErrorLearningError as exc:
        print(colorize(str(exc), "red"), file=sys.stderr)
        return 1

    entries = store["entries"]
    assert isinstance(entries, list)

    if args.command == "add":
        try:
            entry, created = add_entry(
                args.log_path,
                args.error_category,
                args.error_message,
                args.lesson_learned,
                resolved=args.resolved,
            )
        except ErrorLearningError as exc:
            print(colorize(str(exc), "red"), file=sys.stderr)
            return 1

        if created:
            print(colorize("Saved error learning entry.", "green"))
        else:
            print(colorize("Duplicate entry detected; existing learning kept.", "yellow"))
        print(format_entry(entry))
        return 0

    if args.command == "watch":
        watch_log(args.log_path, poll_interval=args.interval, state_path=args.state_path)
        return 0

    validated_entries = [validate_entry(entry) for entry in entries]

    if args.command == "list":
        print_entries(validated_entries, heading="OpenClaw Error Learnings")
        return 0

    if args.command == "stats":
        print_stats(validated_entries)
        return 0

    if args.command == "search":
        matches = search_entries(validated_entries, args.query, limit=max(args.limit, 1))
        print_entries(matches, heading=f"Search Results: {args.query}")
        return 0

    if args.command == "analyze":
        analyze_patterns(validated_entries)
        return 0

    print(colorize(f"Unsupported command: {args.command}", "red"), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
