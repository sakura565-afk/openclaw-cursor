"""Apply the local patches to ComfyUI-LatentSyncWrapper.

The wrapper lives inside the ComfyUI install, not in this repository, so the
edits would be lost on the next `git pull` of the wrapper. Keep the patched
copies here and re-apply them with this script.

Patches kept (both verified against ComfyUI 0.43.0 on 2026-11-05):

1. `nodes.py`
   - `torchvision.io.write_video` was removed in recent torchvision releases,
     so the wrapper's `except TypeError` never caught the AttributeError and
     the node died. Catch both and fall back to imageio.
   - The same for `torchvision.io.read_video`: fall back to the wrapper's own
     `read_video` (frames come back as `f h w c` and must become `f c h w`).

2. `latentsync/pipelines/lipsync_pipeline.py`
   - Free the per-chunk tensors. Without this the CUDA caching allocator keeps
     the previous chunk's latents, masks and audio embeddings alive and a
     175-frame clip OOMs on a 16 GB card around chunk 10-12 of 12. The
     failure is silent: ComfyUI exits with code 1 and no traceback.

Usage:
  python scripts/apply_latentsync_patches.py            # apply if needed
  python scripts/apply_latentsync_patches.py --check    # report only
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PATCHED = HERE / "latentsync_patches"


def _wrapper_dir() -> Path | None:
    env = os.environ.get("LATENTSYNC_WRAPPER", "").strip()
    if env:
        p = Path(env)
        return p if p.is_dir() else None
    # look next to the running ComfyUI: <comfy>/custom_nodes/ComfyUI-LatentSyncWrapper
    for base in (Path(os.environ.get("COMFY_ROOT", "") or os.getcwd()),):
        cand = base / "custom_nodes" / "ComfyUI-LatentSyncWrapper"
        if cand.is_dir():
            return cand
    return None


def _markers() -> list[tuple[str, str]]:
    """(relative path, marker that must be present for the patch to be applied)."""
    return [
        ("nodes.py", "except (TypeError, AttributeError) as e:"),
        ("nodes.py", "from latentsync.utils.util import read_video as _read_video"),
        ("latentsync/pipelines/lipsync_pipeline.py", "torch.cuda.empty_cache()"),
    ]


def _rel_of(patched_file: Path) -> str:
    """The patched copies keep the wrapper's relative path in their name."""
    name = patched_file.name
    # latentsync_pipelines.lipsync_pipeline.py.patched -> latentsync/pipelines/lipsync_pipeline.py
    if name.startswith("latentsync_"):
        rest = name[len("latentsync_"):].replace(".patched", "")
        head, _, tail = rest.partition(".")
        return f"latentsync/{head}/{tail}"
    return name.replace(".patched", "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="only report")
    ap.add_argument("--force", action="store_true", help="copy even if markers match")
    a = ap.parse_args()

    wrapper = _wrapper_dir()
    if wrapper is None:
        raise SystemExit("не найден ComfyUI-LatentSyncWrapper: задай LATENTSYNC_WRAPPER")

    patched_files = sorted(PATCHED.glob("*.patched"))
    if not patched_files:
        raise SystemExit(f"нет патчей в {PATCHED}")

    changed = []
    for pf in patched_files:
        rel = _rel_of(pf)
        target = wrapper / rel
        if not target.exists():
            print(f"  пропуск {rel}: нет файла в обёртке")
            continue
        current = target.read_text(encoding="utf-8", errors="ignore")
        want = pf.read_text(encoding="utf-8", errors="ignore")
        if current == want:
            print(f"  {rel}: уже пропатчен ✓✓")
            continue
        already = all(m in current for _, m in _markers() if _ == rel)
        if already and not a.force:
            print(f"  {rel}: патч уже применён (маркеры на месте) ✓✓")
            continue
        changed.append(rel)
        if a.check:
            print(f"  {rel}: НУЖЕН патч")
            continue
        backup = target.with_suffix(target.suffix + ".prepatch")
        if not backup.exists():
            shutil.copy2(target, backup)
        target.write_text(want, encoding="utf-8")
        print(f"  {rel}: пропатчен ✓✓ (бэкап {backup.name})")

    if a.check:
        return 0
    if changed:
        print(f"перезапусти ComfyUI, чтобы нода перечитала модуль: {', '.join(changed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
