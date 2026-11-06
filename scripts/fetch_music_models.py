"""Download the open music models ComfyUI needs, into the ComfyUI model tree.

ComfyUI already ships the ACE-Step 1.5 and YuE 2 nodes; only the weights are
missing. The weights go where extra_model_paths.yaml points ComfyUI at its
model store, so nothing lands on a nearly full drive.

The target root comes from COMFY_MODELS_ROOT, or from the extra_model_paths
entry ComfyUI itself reads, so no machine-specific path is baked in.

Usage:
  python scripts/fetch_music_models.py --ace-step
  python scripts/fetch_music_models.py --ace-step --dry-run
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import time
from pathlib import Path

REPO = "Comfy-Org/ACE-Step-1.5"
# (relative path in the repo, subfolder under the ComfyUI model root)
ACE_FILES = [
    ("split_files/diffusion_models/acestep_v1.5_turbo.safetensors", "diffusion_models"),
    ("split_files/diffusion_models/acestep_v1.5_base.safetensors", "diffusion_models"),
    ("split_files/text_encoders/qwen_0.6b_ace15.safetensors", "text_encoders"),
    ("split_files/text_encoders/qwen_1.7b_ace15.safetensors", "text_encoders"),
    ("split_files/text_encoders/qwen_4b_ace15.safetensors", "text_encoders"),
    ("split_files/vae/ace_1.5_vae.safetensors", "vae"),
]


def _token() -> str:
    env = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if env:
        return env.strip()
    env_file = os.path.join(os.path.expanduser("~"), "AppData", "Local", "hermes", ".env")
    if os.path.exists(env_file):
        with open(env_file, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("HF_TOKEN="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _model_root() -> str:
    """Where ComfyUI keeps diffusion_models/text_encoders/vae.

    Read from the extra_model_paths.yaml ComfyUI actually loads, so the
    script follows the install instead of hard-coding a path.
    """
    configured = os.environ.get("COMFY_MODELS_ROOT", "").strip()
    if configured:
        return configured
    for candidate in _extra_model_paths_files():
        try:
            with open(candidate, encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
        except OSError:
            continue
        m = re.search(r"base_path:\s*([^\s]+)", text)
        if m:
            root = m.group(1).strip()
            if os.path.isdir(root):
                return root
    return ""


def _extra_model_paths_files() -> list[str]:
    """Candidate extra_model_paths.yaml locations, in priority order."""
    out: list[str] = []
    for key in ("COMFY_ROOT", "COMFY_MODELS_CONFIG"):
        value = os.environ.get(key, "").strip()
        if not value:
            continue
        if value.endswith((".yaml", ".yml")):
            out.append(value)
        else:
            out.append(os.path.join(value, "models", "extra_model_paths.yaml"))
            out.append(os.path.join(value, "extra_model_paths.yaml"))
    return out


def plan(root: str) -> list[tuple[str, Path]]:
    out = []
    for rel, folder in ACE_FILES:
        out.append((rel, Path(root) / folder / Path(rel).name))
    return out


def _repo_layout_dir(local_dir: Path, rel: str) -> Path:
    """Where huggingface_hub will put the file inside ``local_dir``.

    The hub keeps the repository layout under ``local_dir``, so the file lands
    in ``split_files/<kind>/<name>``. The hub creates that directory itself
    only for the flat layout - with ``local_dir`` it expects the parent to
    exist, and the copy then fails with FileNotFoundError.
    """
    return Path(local_dir) / Path(rel).parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ace-step", action="store_true")
    ap.add_argument("--root", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default="", help="substring filter on the file name")
    ap.add_argument("--force", action="store_true",
                    help="download again even if the file is already there")
    a = ap.parse_args()

    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        raise SystemExit("нужен huggingface_hub")

    token = _token()
    if token:
        os.environ["HF_TOKEN"] = token

    root = a.root or _model_root()
    if not root:
        raise SystemExit("не найден каталог моделей: задай COMFY_MODELS_ROOT")
    items = [(rel, dst) for rel, dst in plan(root) if not a.only or a.only in rel]
    for rel, dst in items:
        size = 0
        flat = dst.parent / Path(rel).name
        print(f"  {rel} -> {dst}")
        if a.dry_run:
            continue
        if flat.exists() and not a.force:
            # The file is already in place. Downloading it again would burn
            # gigabytes to produce the same bytes.
            size = os.path.getsize(flat)
            print(f"    уже на месте: {size/1024**3:.2f} ГБ - пропускаю")
            print(f"    итог: {flat} ({size/1024**3:.2f} ГБ)")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        # huggingface_hub keeps the repository layout under local_dir and
        # expects the nested directory to be there already.
        layout_dir = _repo_layout_dir(dst.parent, rel)
        layout_dir.mkdir(parents=True, exist_ok=True)
        started = time.time()
        got = hf_hub_download(
            repo_id=REPO,
            filename=rel,
            local_dir=str(dst.parent),
            local_dir_use_symlinks=False,
            resume_download=True,
        )
        size = os.path.getsize(got) if os.path.exists(got) else 0
        print(f"    готово: {size/1024**3:.2f} ГБ за {time.time()-started:.0f} с")
        # hf_hub_download keeps the repo layout under local_dir, so the file
        # lands in split_files/<kind>/<name>; move it next to its siblings
        # where ComfyUI scans for models.
        got_path = Path(got)
        if got_path.exists() and os.path.abspath(got_path) != os.path.abspath(str(flat)):
            if flat.exists():
                os.remove(flat)
            shutil.move(str(got_path), str(flat))
            for folder in (got_path.parent, got_path.parent.parent):
                try:
                    if folder.is_dir() and not any(folder.iterdir()):
                        folder.rmdir()
                except OSError:
                    pass
            got = str(flat)
        print(f"    итог: {got} ({size/1024**3:.2f} ГБ)")
    print("DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
