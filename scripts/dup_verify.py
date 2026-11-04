"""Verify duplicates on K: against their G: copies with real SHA-256 hashes.

The name+size criterion finds candidates; only a byte-identical hash proves a
file is a true duplicate. Files whose hash differs are kept, and the report
states exactly which ones and why.

Only reads. Writes a manifest for the delete step. Deletes nothing.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time

MANIFEST = os.environ.get("DUP_MANIFEST", "dup_manifest.json")
OUT = os.environ.get("DUP_VERIFIED", "dup_verified.json")


def sha256(path: str, chunk: int = 4 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def main() -> int:
    with open(MANIFEST, encoding="utf-8") as fh:
        m = json.load(fh)
    dups = m["duplicate_files_on_k"]
    print(f"candidates from name+size: {len(dups)}", flush=True)
    t0 = time.time()
    identical: list[dict] = []
    differing: list[dict] = []
    for i, d in enumerate(dups):
        try:
            hk = sha256(d["path"])
            hg = sha256(d["g_copy"])
        except OSError as exc:
            differing.append({**d, "reason": f"io error: {exc}"})
            continue
        if hk == hg:
            identical.append({**d, "sha256": hk})
        else:
            differing.append({**d, "reason": "hash differs", "k_sha": hk, "g_sha": hg})
        if (i + 1) % 200 == 0:
            done = i + 1
            mb = sum(x["size"] for x in identical) / 2**30
            print(f"  {done}/{len(dups)} checked, identical {len(identical)} "
                  f"({mb:.2f} GiB), {time.time() - t0:.0f}s", flush=True)
    print(f"identical: {len(identical)} "
          f"({sum(x['size'] for x in identical) / 2**30:.2f} GiB)", flush=True)
    print(f"differing/kept: {len(differing)} "
          f"({sum(x['size'] for x in differing) / 2**30:.2f} GiB)", flush=True)
    per_year: dict[str, dict[str, int]] = {}
    for x in identical:
        y = x["path"][len(m["k_root"]):].split(os.sep)[0]
        e = per_year.setdefault(y, {"files": 0, "bytes": 0})
        e["files"] += 1
        e["bytes"] += x["size"]
    for y in sorted(per_year):
        e = per_year[y]
        print(f"  {y}: {e['files']} files, {e['bytes'] / 2**30:.2f} GiB", flush=True)
    out = {
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "manifest": MANIFEST,
        "k_root": m["k_root"],
        "g_root": m["g_root"],
        "verified_identical": identical,
        "kept_differing": differing,
        "per_year_identical_on_k": per_year,
        "total_identical_bytes": sum(x["size"] for x in identical),
        "total_kept_bytes": sum(x["size"] for x in differing),
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print(f"verified manifest -> {OUT}")
    print(f"elapsed {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
