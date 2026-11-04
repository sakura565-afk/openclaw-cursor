"""Inventory cross-disk duplicates between the two USB3 archive disks.

G: and K: hold overlapping photo archives (same years, same file names). K: is
the tight disk (1.5 TB free) and sits on the slow 35 MB/s JMicron bus, G: has
8.8 TB free at 228 MB/s. So the keeper is G: and the removable copies are the
ones on K:.

Method:
  1. index G: by (name, size)
  2. walk K:, a K: file is a duplicate iff G: holds a file with the same name
     and the same size
  3. verify a random sample by SHA-256 to prove the name+size criterion is
     actually identifying identical bytes
  4. write the exact duplicate paths to a manifest - nothing is deleted here

No absolute paths, no deletions: this only reads and reports.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import sys
import time

# Roots come from the environment: no machine-specific paths in the repo.
G_ROOT = os.environ.get("DUP_G_ROOT", "")
K_ROOT = os.environ.get("DUP_K_ROOT", "")
MANIFEST = os.environ.get("DUP_MANIFEST", "dup_manifest.json")
SAMPLE = int(os.environ.get("DUP_SAMPLE", "40"))
SKIP_DIRS = {"$RECYCLE.BIN", "System Volume Information", ".git", "node_modules"}

if not G_ROOT or not K_ROOT:
    raise SystemExit(
        "Set DUP_G_ROOT and DUP_K_ROOT to the two roots to compare, "
        "for example DUP_G_ROOT=<drive>:<path> DUP_K_ROOT=<drive>:<path>")


def walk_files(root: str, years: list[str]) -> list[tuple[str, int, str]]:
    out: list[tuple[str, int, str]] = []
    for y in years:
        base = os.path.join(root, y)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                p = os.path.join(dirpath, name)
                try:
                    size = os.stat(p).st_size
                except OSError:
                    continue
                out.append((name, size, p))
    return out


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
    t0 = time.time()
    g_years = sorted(d for d in os.listdir(G_ROOT)
                     if d.isdigit() and os.path.isdir(os.path.join(G_ROOT, d)))
    k_years = sorted(d for d in os.listdir(K_ROOT)
                     if d.isdigit() and os.path.isdir(os.path.join(K_ROOT, d)))
    common = [y for y in g_years if y in k_years]
    print(f"years on G: {g_years}")
    print(f"years on K: {k_years}")
    print(f"common years: {common}", flush=True)

    g_files = walk_files(G_ROOT, common)
    print(f"G: files in common years: {len(g_files)} "
          f"({sum(s for _, s, _ in g_files) / 2**30:.1f} GiB)", flush=True)

    g_index: dict[tuple[str, int], str] = {}
    for name, size, p in g_files:
        g_index.setdefault((name, size), p)

    k_files = walk_files(K_ROOT, common)
    dup: list[tuple[str, int, str]] = []
    uniq: list[tuple[str, int, str]] = []
    for name, size, p in k_files:
        (dup if (name, size) in g_index else uniq).append((name, size, p))
    print(f"K: files in common years: {len(k_files)} "
          f"({sum(s for _, s, _ in k_files) / 2**30:.1f} GiB)", flush=True)
    print(f"  duplicates on K (also on G): {len(dup)} "
          f"({sum(s for _, s, _ in dup) / 2**30:.2f} GiB)", flush=True)
    print(f"  unique on K: {len(uniq)} "
          f"({sum(s for _, s, _ in uniq) / 2**30:.2f} GiB)", flush=True)

    # Prove the name+size criterion finds identical bytes.
    sample = random.sample(dup, min(SAMPLE, len(dup))) if dup else []
    same = 0
    checked = []
    for name, size, kp in sample:
        gp = g_index[(name, size)]
        try:
            if sha256(kp) == sha256(gp):
                same += 1
        except OSError:
            continue
        checked.append({"name": name, "size": size, "k": kp, "g": gp})
    print(f"hash sample: {same}/{len(sample)} identical bytes", flush=True)

    per_year: dict[str, dict[str, int]] = {}
    for name, size, p in dup:
        y = p[len(K_ROOT):].split(os.sep)[0]
        e = per_year.setdefault(y, {"files": 0, "bytes": 0})
        e["files"] += 1
        e["bytes"] += size
    for y in sorted(per_year):
        e = per_year[y]
        print(f"  {y}: {e['files']} files, {e['bytes'] / 2**30:.2f} GiB", flush=True)

    manifest = {
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "g_root": G_ROOT,
        "k_root": K_ROOT,
        "common_years": common,
        "hash_sample_size": len(sample),
        "hash_sample_identical": same,
        "hash_sample_examples": checked[:10],
        "per_year_duplicates_on_k": per_year,
        "duplicate_files_on_k": [
            {"path": p, "size": s, "g_copy": g_index[(n, s)]} for n, s, p in dup],
        "unique_files_on_k_kept": [
            {"path": p, "size": s} for _, s, p in uniq],
    }
    with open(MANIFEST, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)
    print(f"manifest -> {MANIFEST}")
    print(f"elapsed {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
