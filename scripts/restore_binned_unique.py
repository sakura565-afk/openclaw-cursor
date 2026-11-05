"""Restore files that the duplicate cleanup moved to the Recycle Bin by mistake.

The cleanup pass matched candidates by name and size. That is not proof of
identity: two camera raw files can share a name and a size while holding
different pixels. The SHA-256 verification pass found 28 such files on K: -
their content differs from the G: keeper with the same name, so they are
unique data and must not have been removed.

Those files were still in the Recycle Bin, so nothing was lost. This script
finds them again by their original path (the Recycle Bin keeps it in the
`$I` metadata record), copies them back, and verifies the SHA-256 against
the verification manifest.

Usage:
  python scripts/restore_binned_unique.py --manifest <path to dup_verified.json>
  python scripts/restore_binned_unique.py --manifest <path> --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import sys
from pathlib import Path


def parse_i_record(blob: bytes) -> str:
    """Original path from a Recycle Bin `$I` metadata record (version 2).

    The header is 24 bytes: version, file size, deletion time. The path
    follows as a null-terminated UTF-16LE string.
    """
    for off in (24, 20, 28, 32, 16, 40):
        if off >= len(blob):
            continue
        txt = blob[off:].decode("utf-16-le", errors="ignore")
        out = []
        for ch in txt:
            if ch == "\x00" or ord(ch) < 32:
                break
            out.append(ch)
        s = "".join(out).strip()
        if len(s) > 12 and ":" in s and "\\" in s:
            return s
    return ""


def scan_bin(binroot: Path) -> dict[str, tuple[str, int]]:
    """Map original path -> (recycled copy path, size)."""
    found: dict[str, tuple[str, int]] = {}
    if not binroot.exists():
        return found
    for sid in os.listdir(binroot):
        if sid.startswith("$"):
            continue
        sd = binroot / sid
        try:
            entries = os.listdir(sd)
        except OSError:
            continue
        for name in entries:
            if not name.startswith("$I"):
                continue
            try:
                blob = (sd / name).read_bytes()
            except OSError:
                continue
            orig = parse_i_record(blob)
            if not orig:
                continue
            rpath = sd / ("$R" + name[2:])
            if rpath.exists():
                found[orig.lower()] = (str(rpath), rpath.stat().st_size)
    return found


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path, required=True,
                    help="dup_verified.json produced by scripts/dup_verify.py")
    ap.add_argument("--bin-root", default=None,
                    help="Recycle Bin root; derived from the manifest k_root")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json-out", type=Path, default=None)
    a = ap.parse_args()

    if not a.manifest.exists():
        print(f"нет манифеста: {a.manifest}", file=sys.stderr)
        return 2
    doc = json.loads(a.manifest.read_text(encoding="utf-8"))
    kept = doc.get("kept_differing") or []
    if not kept:
        print("в манифесте нет kept_differing", file=sys.stderr)
        return 2

    k_root = doc.get("k_root")
    if not k_root:
        print("в манифесте нет k_root - укажите корзину через --bin-root",
              file=sys.stderr)
        return 2
    bin_root = Path(a.bin_root) if a.bin_root else Path(k_root) / "$RECYCLE.BIN"
    print(f"корзина: {bin_root}")

    binmap = scan_bin(bin_root)
    print(f"записей в корзине разобрано: {len(binmap)}")

    restored, present, unmatched = [], [], []
    for e in kept:
        key = e["path"].replace("/", "\\").lower()
        hit = binmap.get(key)
        if not hit or hit[1] != e["size"]:
            unmatched.append(e["path"])
            continue
        dest = e["path"]
        if os.path.exists(dest):
            present.append(dest)
            continue
        if not a.dry_run:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(hit[0], dest)
        restored.append(dest)

    print(f"уникальных в манифесте: {len(kept)}")
    print(f"восстановлено: {len(restored)} | уже на месте: {len(present)} | не найдено: {len(unmatched)}")
    for u in unmatched[:5]:
        print(f"  не найдено: {u}")

    # verification
    bad = []
    for e in kept:
        p = e["path"]
        if not os.path.exists(p):
            bad.append(("MISSING", p))
        elif os.path.getsize(p) != e["size"]:
            bad.append(("SIZE", p))
        elif sha256_of(p) != e["k_sha"]:
            bad.append(("HASH", p))
    print(f"контроль SHA-256: проблем {len(bad)}")
    for b in bad[:5]:
        print(f"  {b[0]}: {b[1]}")

    res = {
        "manifest": str(a.manifest),
        "bin_root": str(bin_root),
        "unique_in_manifest": len(kept),
        "restored": len(restored),
        "already_present": len(present),
        "unmatched": unmatched,
        "verification_problems": [list(b) for b in bad],
        "dry_run": a.dry_run,
    }
    if a.json_out:
        a.json_out.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"отчёт: {a.json_out}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
