# -*- coding: utf-8 -*-
"""Download MiniMax H3 LoRAs from the EllaPriest45/MinimaxH3_Actions repo.

Usage:
  python scripts/h3_lora_fetch.py --priority 1        # only P1
  python scripts/h3_lora_fetch.py --priority 1 2      # P1 and P2
  python scripts/h3_lora_fetch.py --all
  python scripts/h3_lora_fetch.py --priority 1 --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request

REPO = "EllaPriest45/MinimaxH3_Actions"
DEST = r"G:\AI\Models\comfyui\loras"
MANIFEST = r"C:\Users\user\AppData\Local\hermes\cache\scratch\h3_lora_missing.json"

# Priority groups mirror what the pipeline actually needs:
#   1 = realism / detail / motion-stability / face  (our bottleneck)
#   2 = camera / anatomy / action / utility
#   3 = everything else (specialised scenes)
PRIORITY_RULES = [
    (r"add micro details|micro details", 1),
    (r"a little more sharpness|sharpness", 1),
    (r"restore enhance improve", 1),
    (r"photorealistic|ph0t0r34l", 1),
    (r"realism slider|icy realism|icyrealism", 1),
    (r"perfect skin|perfect eyes|perfe8ct", 1),
    (r"motion continuity|can't stop shaking|slow and smooth|spatial and physics", 1),
    (r"natural face|best face swap|character swap", 1),
    (r"skin", 1),
    (r"finalcut|vfx-edit|intricate composition|parasyte", 2),
    (r"edge time|time-freeze|timefreeze|motion-speed|fast motion|datamosh", 2),
    (r"shaky|telephoto|drone|crossview|pov", 2),
    (r"breast.*physics|bouncing boobs|drop physics", 2),
    (r"male anatomy|hmpussy|innie|synth pussy|pussy & anus", 2),
    (r"hmnsfw|fingering|beanflk|jerking off|masturbat|dildo|side splits", 2),
]


def priority(name: str) -> int:
    import re
    for rx, p in PRIORITY_RULES:
        if re.search(rx, name, re.I):
            return p
    return 3


def api_tree() -> list:
    url = f"https://huggingface.co/api/models/{REPO}/tree/main?recursive=true"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode("utf-8"))
    return [f for f in data if f.get("type") == "file" and f["path"].endswith(".safetensors")]


def local_names() -> set:
    import re
    out = set()
    for d in (r"C:\AI\Models\comfyui\loras",
              r"E:\ComfyUI_windows_portable_nvidia_cu126\ComfyUI_windows_portable\ComfyUI\models\loras",
              r"C:\Users\user\AppData\Local\hermes\cache\models\H3-LoRA",
              DEST):
        if os.path.isdir(d):
            for f in os.listdir(d):
                if f.endswith((".safetensors", ".ckpt")):
                    out.add(re.sub(r"[^a-z0-9]+", "", f.lower().replace(".safetensors", "")))
    return out


def download(path: str, size: int, dest: str = DEST) -> bool:
    os.makedirs(dest, exist_ok=True)
    target = os.path.join(dest, os.path.basename(path))
    if os.path.exists(target) and os.path.getsize(target) >= size * 0.98:
        print(f"  уже есть: {os.path.basename(path)[:70]}", flush=True)
        return True
    url = f"https://huggingface.co/{REPO}/resolve/main/{urllib.parse.quote(path)}"
    tmp = target + ".part"
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as fh:
                total = 0
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    fh.write(chunk)
                    total += len(chunk)
            if size and total < size * 0.98:
                raise IOError(f"short read {total}/{size}")
            os.replace(tmp, target)
            print(f"  OK {total/1e9:.2f} GB  {os.path.basename(path)[:70]}", flush=True)
            return True
        except Exception as e:
            print(f"  retry {attempt+1}: {os.path.basename(path)[:55]} -> {str(e)[:70]}", flush=True)
            time.sleep(6)
    if os.path.exists(tmp):
        try:
            os.remove(tmp)
        except OSError:
            pass
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--priority", type=int, nargs="*", default=[1])
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--dest", default=DEST)
    args = ap.parse_args()

    files = api_tree()
    have = local_names()
    import re
    todo = []
    for f in files:
        b = os.path.basename(f["path"])
        n = re.sub(r"[^a-z0-9]+", "", b.lower().replace(".safetensors", ""))
        if n in have:
            continue
        stem = re.sub(r"[^a-z0-9]+", "", re.split(r"[,-]", b[:-11])[0].strip().lower())
        if stem and any(stem in h or h in stem for h in have if len(h) > 12):
            continue
        p = 3 if args.all else (priority(b) if priority(b) in args.priority else None)
        if p is None:
            continue
        todo.append((p, f["path"], f.get("size", 0)))
    todo.sort(key=lambda x: (x[0], -x[2]))
    total = sum(s for _, _, s in todo)
    print(f"к загрузке: {len(todo)} файлов, {total/1e9:.1f} ГБ -> {args.dest}", flush=True)
    if args.dry_run:
        for p, path, s in todo:
            print(f"  P{p} {s/1e9:5.2f} GB  {os.path.basename(path)[:90]}")
        return 0

    ok = fail = 0
    for p, path, s in todo:
        if download(path, s, args.dest):
            ok += 1
        else:
            fail += 1
    print(f"готово: {ok} ok, {fail} fail", flush=True)
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
