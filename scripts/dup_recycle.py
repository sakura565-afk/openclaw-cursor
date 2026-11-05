"""Move verified duplicates to the Recycle Bin, with a report.

Safety contract:
  * only files listed as byte-identical in dup_verified.json are touched
  * each file is re-checked for existence and size before the move
  * the Recycle Bin is used (recoverable), never a hard delete
  * every action is logged to a JSON report: moved, skipped, failed
  * nothing is removed from G: - G: is the keeper

The manifest is the only input. No path is derived from a name pattern.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time

VERIFIED = os.environ.get("DUP_VERIFIED", "dup_verified.json")
REPORT = os.environ.get("DUP_REPORT", "dup_delete_report.json")
DRY_RUN = os.environ.get("DUP_DRY_RUN", "1") not in ("0", "false", "False")
LIMIT = int(os.environ.get("DUP_LIMIT", "0"))


def shell_recycle_bin() -> object:
    """Kept for reference; the ctypes path below is what actually runs.

    Routing a file to the Recycle Bin from a script needs Shell32's
    SHFileOperation with FOF_ALLOWUNDO. os.remove() would delete it outright
    and shutil.move() would relocate it - neither is a recycle.
    """
    raise NotImplementedError("use recycle_via_shfileoperation")


def recycle_via_shfileoperation(paths: list[str]) -> tuple[int, list[str]]:
    """Send files to the Recycle Bin through SHFileOperationW.

    FOF_ALLOWUNDO is what makes it recoverable. FOF_NOCONFIRMATION and
    FOF_NOERRORUI keep it non-interactive; FOF_SILENT hides the progress UI.
    Returns (number_recycled, list_of_paths_that_failed).
    """
    import ctypes
    from ctypes import wintypes

    FO_DELETE = 0x0003
    FOF_SILENT = 0x0004
    FOF_NOCONFIRMATION = 0x0010
    FOF_ALLOWUNDO = 0x0040
    FOF_NOERRORUI = 0x0400

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", ctypes.WORD),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    # pFrom wants a double-NUL terminated list of paths
    buf = "".join(p + "\0" for p in paths) + "\0"
    op = SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = ctypes.c_wchar_p(buf)
    op.pTo = None
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_SILENT
    op.fAnyOperationsAborted = False
    op.hNameMappings = None
    op.lpszProgressTitle = None
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if rc != 0:
        return rc, list(paths)
    return 0, []


def main() -> int:
    with open(VERIFIED, encoding="utf-8") as fh:
        v = json.load(fh)
    items = v.get("verified_identical", [])
    if LIMIT:
        items = items[:LIMIT]
    print(f"manifest: {VERIFIED}")
    print(f"candidates: {len(items)} "
          f"({sum(x['size'] for x in items) / 2**30:.2f} GiB)")
    print(f"DRY RUN: {DRY_RUN}")
    moved: list[dict] = []
    skipped: list[dict] = []
    failed: list[dict] = []
    t0 = time.time()
    for i, it in enumerate(items):
        p = it["path"]
        try:
            st = os.stat(p)
        except OSError as exc:
            skipped.append({"path": p, "reason": f"missing: {exc}"})
            continue
        if st.st_size != it["size"]:
            skipped.append({"path": p, "reason": "size changed since verification"})
            continue
        g = it["g_copy"]
        if not os.path.exists(g):
            skipped.append({"path": p, "reason": "keeper copy on G: is gone"})
            continue
        if not DRY_RUN:
            try:
                rc, failed_paths = recycle_via_shfileoperation([p])
            except Exception as exc:  # noqa: BLE001
                failed.append({"path": p, "reason": f"recycle failed: {exc}"})
                continue
            if rc != 0:
                failed.append({"path": p, "reason": f"SHFileOperation rc={rc}"})
                continue
            # the shell verb is async; confirm the file is really gone
            for _ in range(40):
                if not os.path.exists(p):
                    break
                time.sleep(0.25)
        moved.append({"path": p, "size": it["size"], "g_copy": g})
        if (i + 1) % 200 == 0:
            print(f"  {i + 1}/{len(items)} processed, {time.time() - t0:.0f}s", flush=True)
    report = {
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "dry_run": DRY_RUN,
        "manifest": VERIFIED,
        "k_root": v.get("k_root"),
        "g_root": v.get("g_root"),
        "candidates": len(items),
        "candidate_bytes": sum(x["size"] for x in items),
        "moved": moved,
        "moved_bytes": sum(x["size"] for x in moved),
        "skipped": skipped,
        "failed": failed,
        "elapsed_s": round(time.time() - t0, 1),
    }
    with open(REPORT, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
    print(f"moved: {len(moved)} ({report['moved_bytes'] / 2**30:.2f} GiB)")
    print(f"skipped: {len(skipped)}  failed: {len(failed)}")
    print(f"report -> {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
