"""Move verified duplicates to the Recycle Bin, with a report.

Safety rules this script enforces:
  * only paths listed in the duplicate manifest are touched;
  * the K: copy must still exist and match the recorded size;
  * the G: keeper copy must still exist and match the recorded size;
  * optionally the SHA-256 of both copies is compared before moving anything;
  * nothing is deleted for real - everything goes to the Recycle Bin, so the
    user can restore it.

Usage:
  python scripts/trash_duplicates.py --manifest <manifest.json> --limit 20 --dry-run
  python scripts/trash_duplicates.py --manifest <manifest.json> --limit 20
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import time
from pathlib import Path

try:
    from send2trash import send2trash
except ImportError:  # pragma: no cover
    send2trash = None


def sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def check_entry(entry: dict, verify_hash: bool) -> tuple[bool, str]:
    """Return (ok, reason). Only verified pairs may be trashed."""
    k = Path(entry["path"])
    g = Path(entry["g_copy"])
    size = int(entry["size"])

    if not k.exists():
        return False, "k_missing"
    if not g.exists():
        return False, "g_missing"
    ks = k.stat().st_size
    gs = g.stat().st_size
    if ks != size:
        return False, f"k_size_mismatch({ks}!={size})"
    if gs != size:
        return False, f"g_size_mismatch({gs}!={size})"
    if verify_hash:
        if sha256(k) != sha256(g):
            return False, "hash_mismatch"
    return True, "ok"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--limit", type=int, default=0, help="0 = all")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify-hash", action=argparse.BooleanOptionalAction, default=True,
                    help="compare SHA-256 of both copies before moving (default: on). "
                         "Name plus size is NOT proof of identity: on this archive 28 "
                         "camera raw files shared a name and a size with the keeper "
                         "while holding different pixels, and they were removed "
                         "before the full verification pass caught them.")
    ap.add_argument("--hash-sample", type=int, default=0,
                    help="only SHA-256 check this many entries instead of all of "
                         "them (0 = all). Sampling is for dry runs only: an "
                         "unchecked entry is removed on name and size alone.")
    ap.add_argument("--skip", type=int, default=0,
                    help="skip the first N entries (for resuming a run)")
    ap.add_argument("--report", default="")
    a = ap.parse_args()

    if send2trash is None:
        raise SystemExit("нужен send2trash: uv pip install send2trash")

    manifest = json.load(open(a.manifest, encoding="utf-8"))
    entries = manifest.get("duplicate_files_on_k") or []
    if a.skip:
        entries = entries[a.skip:]
    if a.limit:
        entries = entries[:a.limit]

    # entries to hash-check
    hash_set: set[int] = set()
    if a.hash_sample:
        rng = random.Random(1234)
        idx = rng.sample(range(len(entries)), min(a.hash_sample, len(entries)))
        hash_set = set(idx)
        print(f"ВНИМАНИЕ: SHA-256 проверяется только у {len(hash_set)} записей из "
              f"{len(entries)}; остальные удаляются по имени и размеру. Это не "
              f"доказательство идентичности - используйте только для пробного прогона.",
              file=sys.stderr, flush=True)

    rows = []
    freed = 0
    hashed = 0
    started = time.time()
    for i, entry in enumerate(entries, 1):
        # a sample limits the hash check to the sampled entries; without one,
        # every entry is hashed
        verify = a.verify_hash and (not a.hash_sample or i in hash_set)
        ok, reason = check_entry(entry, verify)
        if verify and ok:
            hashed += 1
        row = {"path": entry["path"], "g_copy": entry["g_copy"], "size": entry["size"],
               "checked": reason, "hashed": verify, "trashed": False}
        if not ok:
            row["skipped"] = reason
            rows.append(row)
            continue
        if a.dry_run:
            row["skipped"] = "dry_run"
            rows.append(row)
            continue
        try:
            send2trash(str(Path(entry["path"])))
            row["trashed"] = True
            freed += int(entry["size"])
        except Exception as exc:  # pragma: no cover - depends on the OS.
            row["skipped"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        rows.append(row)
        if i % 200 == 0:
            print(f"  ... {i}/{len(entries)} freed={freed/1024**3:.1f} GiB "
                  f"({time.time()-started:.0f}s)", flush=True)

    report = {
        "manifest": a.manifest,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "dry_run": a.dry_run,
        "verify_hash": a.verify_hash,
        "hash_sample": a.hash_sample,
        "hash_verified_count": hashed,
        "skip": a.skip,
        "candidates": len(entries),
        "trashed": sum(1 for r in rows if r["trashed"]),
        "skipped": sum(1 for r in rows if not r["trashed"]),
        "bytes_freed": freed,
        "elapsed_s": round(time.time() - started, 1),
        "rows": rows,
    }
    out = a.report or f"trash_report_{int(time.time())}.json"
    Path(out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "rows"},
                     ensure_ascii=False, indent=1))
    print(f"report: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
