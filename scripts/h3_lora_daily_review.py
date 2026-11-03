# -*- coding: utf-8 -*-
"""Daily review of MiniMax H3 LoRAs: what is new, and what is actually useful.

The old daily check (`check_new_h3_loras.py`) only listed file names that were
not on disk yet. That answered "what appeared?" but not "should I care?", and
it re-printed the same names every day because nothing was ever marked as
reviewed. This script does both halves:

NEW (from the internet)
  * pulls the full file list of the H3 LoRA mirrors on HuggingFace
  * pulls the newest CivArchive cards
  * diffs both against what is on disk AND against a persistent ledger, so a
    LoRA is reported once, not every morning
  * ranks every new entry by usefulness for OUR pipeline

HAVE (on disk)
  * re-reads the local catalogue
  * flags LoRAs we have but never benchmarked
  * flags benchmarked LoRAs that under-performed, so they can be dropped
  * reports which priority class still has gaps

Usefulness ranking
-------------------
A LoRA is not useful because it exists; it is useful when it attacks a
measured weakness of this pipeline. Our measurements:

  * skin micro-texture in video is the binding constraint - a still scores
    9.2/10, the best video frame only 8.0/10, and H3 renders read as waxy
  * temporal flicker is the second constraint - every detail pass that raises
    skin texture also raises the HF temporal delta (+13..+169%)
  * face drift is the third - H3 10.62 vs FreeVideo high 15.20 mean drift

So LoRAs that only add style, or only add motion complexity, are ranked below
the ones that touch texture, temporal stability and face fidelity - even if
their community rating is higher.

Usage:
  python scripts/h3_lora_daily_review.py                # report, print digest
  python scripts/h3_lora_daily_review.py --json-out out.json
  python scripts/h3_lora_daily_review.py --no-network   # local audit only
  python scripts/h3_lora_daily_review.py --mark-reviewed "Add Micro Details"
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

SCRATCH = os.environ.get(
    "HERMES_SCRATCH_DIR",
    os.path.join(os.path.expanduser("~"), "AppData", "Local", "hermes", "cache", "scratch"),
)
LEDGER = os.path.join(SCRATCH, "h3_lora_ledger.json")
BENCH_DIR = os.path.join(SCRATCH, "h3_lora_bench")
MANIFEST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "scripts", "h3_lora_manifest.json")

HF_REPOS = [
    "EllaPriest45/MinimaxH3_Actions",
    "Comfy-Org/flux1-dev",  # sanity: unrelated, filtered out by the H3 filter
]
CIVARCHIVE_URL = "https://civarchive.com/latest-models"

# Only H3-relevant entries. The mirror also hosts other bases.
H3_HINTS = ("minimax", "h3", "mmh3")


def _force_utf8_stdio() -> None:
    """Make stdout/stderr UTF-8 safe on a Windows console.

    The Windows CI runner's console codec is cp1251/cp437, and printing a
    Cyrillic verdict there raises UnicodeEncodeError and kills the script.
    Reconfiguring the streams is a no-op on Linux and harmless elsewhere.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def _get_json(url: str, timeout: int = 45) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def _get_text(url: str, timeout: int = 45) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


# --------------------------------------------------------------------------
# local side
# --------------------------------------------------------------------------

def local_loras(roots: list) -> dict:
    """Map normalised name -> {path, bytes}. Roots may not exist."""
    out = {}
    for root in roots:
        if not os.path.isdir(root):
            continue
        for f in os.listdir(root):
            if not f.lower().endswith((".safetensors", ".ckpt")):
                continue
            p = os.path.join(root, f)
            try:
                size = os.path.getsize(p)
            except OSError:
                size = 0
            out[re.sub(r"[^a-z0-9]+", "", f.lower().replace(".safetensors", ""))] = {
                "name": f, "path": p, "bytes": size}
    return out


def load_manifest(path: str = MANIFEST) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def load_ledger(path: str = LEDGER) -> dict:
    if not os.path.exists(path):
        return {"seen": {}, "history": []}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        data.setdefault("seen", {})
        data.setdefault("history", [])
        return data
    except (OSError, json.JSONDecodeError):
        return {"seen": {}, "history": []}


def save_ledger(ledger: dict, path: str = LEDGER) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(ledger, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# usefulness model
# --------------------------------------------------------------------------

# Which measured weakness does each LoRA class attack?
CLASS_IMPACT = {
    "detail":    {"weight": 3, "why": "микротекстура кожи - главное узкое место"},
    "realism":   {"weight": 3, "why": "микротекстура и воск кожи - главное узкое место"},
    "motion":    {"weight": 2, "why": "temporal-мерцание и стабильность - второе узкое место"},
    "face":      {"weight": 2, "why": "дрейф лица - третье узкое место"},
    "utility":   {"weight": 1, "why": "утилитарное: режет, чинит, ускоряет"},
    "camera":    {"weight": 1, "why": "камера - к коже отношения не имеет"},
    "anatomy":   {"weight": 1, "why": "анатомия - к коже отношения не имеет"},
    "nsfw":      {"weight": 1, "why": "сюжет - к коже отношения не имеет"},
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower().replace(".safetensors", ""))


def classify(name: str, manifest: dict) -> str:
    """Classify a LoRA by name, preferring the curated manifest.

    The manifest is the source of truth: it was built by reading the actual
    model cards. The regex fallback exists only for LoRAs the manifest does
    not know, and it is deliberately narrow - a loose pattern like "icy" used
    to file "Icy Twerk" and "Icy Ass Spank" under realism.
    """
    low = name.lower()
    loras = manifest.get("loras") or {}
    # longest alias first: "icy_realism" must beat a shorter "icy"
    hits = []
    for order, (key, entry) in enumerate(loras.items()):
        for a in [key] + list(entry.get("aliases") or []):
            if not a:
                continue
            a_low = a.lower()
            if a_low in low:
                # longest alias wins; ties keep MANIFEST order, so the result
                # does not depend on how the categories happen to sort
                hits.append((len(a_low), -order, entry.get("category", "utility")))
    if hits:
        hits.sort(reverse=True)
        return hits[0][2]

    n = _norm(name)
    # "realism" as a WORD in the name or in the trigger list. A description
    # like "Cinematic Realism" inside a name must not file a sex LoRA under
    # realism, so only the head of the name and the trigger tokens count.
    head = re.sub(r"[^a-z0-9]+", " ", os.path.basename(name).lower().split(" - minimaxh3")[0])
    head_words = set(head.split())
    trig = set(re.findall(r"[a-z0-9]+", n.split("minimaxh3")[-1]))
    if ({"realism", "realistic", "ph0t0r34l", "icyrealism"} & (head_words | trig)):
        return "realism"
    if re.search(r"micro.?detail|addmicro|sharp|detail|intricatedetail|"
                 r"restore.?enhance|perfectskin|perfe8ct", n):
        return "detail"
    if re.search(r"motion|shake|smooth|physics|continuity|speed|freeze|"
                 r"timefr33ze|slop.?bounce|jiggle|bounce", n):
        return "motion"
    if re.search(r"face|characterswap|expression|speech|ahgao|ahegao", n):
        return "face"
    if re.search(r"camera|pov|zoom|drone|orbit|shaky|telephoto|crossview", n):
        return "camera"
    if re.search(r"anatom|penis|testicle|pussy|tits|breast|nipple|inniesphere|hmass", n):
        return "anatomy"
    if re.search(r"blowjob|handjob|fingering|masturb|penetrat|deepthroat|"
                 r"cumshoot|cumshot|dildo|jerking|splits", n):
        return "nsfw"
    return "utility"


def usefulness(name: str, manifest: dict) -> dict:
    """Score a LoRA for OUR pipeline. Higher is better."""
    cat = classify(name, manifest)
    impact = CLASS_IMPACT.get(cat, {"weight": 1, "why": "не классифицировано"})
    entry = None
    best = -1
    for key, e in (manifest.get("loras") or {}).items():
        for a in [key] + list(e.get("aliases") or []):
            if a and a.lower() in name.lower() and len(a) > best:
                best = len(a)
                entry = e
    # реализм/деталь-LoRA, меняющие веса, в нашем пайплайне замечены
    # temporal boil - понижаем их приоритет в пользу motion/face
    if entry and entry.get("category") in ("detail", "realism"):
        impact = CLASS_IMPACT.get(entry["category"])
        if impact:
            score = impact["weight"] * 9
    score = impact["weight"] * 10
    notes = [impact["why"]]
    if entry:
        trig = entry.get("trigger_words") or []
        if trig:
            notes.append("триггер: " + ", ".join(trig[:2]))
        st = entry.get("recommended_strength")
        if st is not None:
            notes.append(f"сила {st}")
        if entry.get("temporal_safety"):
            notes.append("temporal-safe")
    # a LoRA whose expected effect fights the flicker constraint is penalised
    eff = (entry or {}).get("expected_effect", "")
    if re.search(r"sharpen|crisp|micro ?detail|contrast", eff, re.I) and cat == "detail":
        notes.append("риск temporal boil - проверять первым")
    verdict = ("ЯДРО" if score >= 25 else
               "ПОЛЕЗНО" if score >= 15 else
               "ОПЦИЯ" if score >= 8 else "ФОН")
    return {"category": cat, "score": score, "verdict": verdict, "notes": notes}


# --------------------------------------------------------------------------
# network side
# --------------------------------------------------------------------------

def fetch_hf(repo: str) -> list:
    try:
        data = _get_json(
            f"https://huggingface.co/api/models/{repo}/tree/main?recursive=true")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        return [{"__error__": f"{repo}: {str(e)[:80]}"}]
    return [{"path": f["path"], "size": f.get("size", 0)}
            for f in data
            if f.get("type") == "file" and f["path"].endswith(".safetensors")]


def fetch_civarchive() -> list:
    """Newest CivArchive cards whose title looks H3-related."""
    try:
        html = _get_text(CIVARCHIVE_URL)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        return [{"__error__": f"CivArchive: {str(e)[:80]}"}]
    out = []
    for name in re.findall(r'<img[^>]*alt="([^"]{3,120})"', html):
        low = name.lower()
        if any(k in low for k in H3_HINTS):
            out.append({"path": name, "size": 0, "source": "CivArchive"})
    return out


def collect_remote() -> tuple:
    """Return (entries, errors). An errored source is a FAILED lookup."""
    entries, errors = [], []
    for repo in HF_REPOS:
        got = fetch_hf(repo)
        if got and "__error__" in got[0]:
            errors.append(got[0]["__error__"])
            continue
        for e in got:
            e["source"] = repo
            entries.append(e)
    civ = fetch_civarchive()
    if civ and "__error__" in civ[0]:
        errors.append(civ[0]["__error__"])
    else:
        entries.extend(civ)
    # keep H3-relevant ones only
    keep = []
    for e in entries:
        low = e["path"].lower()
        if any(h in low for h in H3_HINTS) or e.get("source") == "CivArchive":
            keep.append(e)
    return keep, errors


# --------------------------------------------------------------------------
# bench results (for the HAVE half)
# --------------------------------------------------------------------------

def load_bench_results(bench_dir: str = BENCH_DIR) -> dict:
    """Read bench JSON files: {lora_name: {metric: value}}."""
    out = {}
    if not os.path.isdir(bench_dir):
        return out
    for f in os.listdir(bench_dir):
        if not f.endswith(".json"):
            continue
        p = os.path.join(bench_dir, f)
        try:
            with open(p, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        name = data.get("lora") or data.get("label") or os.path.splitext(f)[0]
        out[name] = data
    return out


def default_lora_roots() -> list:
    """Where the H3 LoRAs live on this box.

    The roots come from the environment so the script stays portable: a
    hard-coded drive letter breaks CI on any other host. H3_LORA_ROOTS is a
    path-separator list; the legacy single-root variable still works.
    """
    env = os.environ.get("H3_LORA_ROOTS", "")
    if env:
        return [p for p in env.split(os.pathsep) if p]
    roots = []
    for var in ("H3_LORA_ROOT_C", "H3_LORA_ROOT_G", "H3_LORA_ROOT_E"):
        v = os.environ.get(var)
        if v:
            roots.append(v)
    if roots:
        return roots
    # Last resort: derive from the ComfyUI installation on THIS machine.
    comfy = os.environ.get("COMFYUI_ROOT")
    if comfy:
        roots.append(os.path.join(comfy, "models", "loras"))
    models = os.environ.get("COMFYUI_MODELS_DIR")
    if models:
        roots.append(os.path.join(models, "loras"))
    return roots


def main() -> int:
    _force_utf8_stdio()
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", action="append", default=[],
                    help="local LoRA dir (repeatable)")
    ap.add_argument("--manifest", default=MANIFEST)
    ap.add_argument("--ledger", default=LEDGER)
    ap.add_argument("--bench-dir", default=BENCH_DIR)
    ap.add_argument("--no-network", action="store_true")
    ap.add_argument("--json-out", default="")
    ap.add_argument("--mark-reviewed", action="append", default=[],
                    help="mark these LoRAs as reviewed (no longer reported as new)")
    ap.add_argument("--max-new", type=int, default=40)
    args = ap.parse_args()

    roots = args.root or default_lora_roots()
    manifest = load_manifest(args.manifest)
    ledger = load_ledger(args.ledger)
    local = local_loras(roots)
    bench = load_bench_results(args.bench_dir)

    for name in args.mark_reviewed:
        key = re.sub(r"[^a-z0-9]+", "", name.lower().replace(".safetensors", ""))
        ledger["seen"][key] = {"name": name, "at": datetime.now(timezone.utc).isoformat(),
                               "how": "manual"}
        print(f"отмечено как просмотренное: {name}", file=sys.stderr)

    errors: list = []
    new_entries: list = []
    if args.no_network:
        errors.append("сеть отключена флагом --no-network")
    else:
        remote, errors = collect_remote()
        seen = ledger["seen"]
        for e in remote:
            key = re.sub(r"[^a-z0-9]+", "", os.path.basename(e["path"]).lower()
                         .replace(".safetensors", ""))
            if key in seen:
                continue
            if key in local:
                # already on disk but never reviewed -> still worth reporting once
                new_entries.append({**e, "key": key, "on_disk": True})
            else:
                new_entries.append({**e, "key": key, "on_disk": False})

    # score the new ones
    for e in new_entries:
        e["usefulness"] = usefulness(os.path.basename(e["path"]), manifest)
    new_entries.sort(key=lambda e: (-e["usefulness"]["score"], e["path"].lower()))

    # HAVE half: local LoRAs never benchmarked, and bench results
    never_benched = []
    for key, info in local.items():
        if not any(bk.lower() in info["name"].lower() or
                   info["name"].lower() in bk.lower() for bk in bench):
            u = usefulness(info["name"], manifest)
            never_benched.append({**info, "key": key, "usefulness": u})
    never_benched.sort(key=lambda e: (-e["usefulness"]["score"], e["name"].lower()))

    # gap analysis: which priority class has no downloaded LoRA at all
    by_class: dict = {}
    for info in local.values():
        cat = classify(info["name"], manifest)
        by_class.setdefault(cat, []).append(info["name"])
    gaps = {c: len(v) for c, v in sorted(by_class.items())}

    stamp = datetime.now(timezone.utc).isoformat()
    result = {
        "generated": stamp,
        "local_count": len(local),
        "bench_count": len(bench),
        "new_count": len(new_entries),
        "new": new_entries[: args.max_new],
        "never_benched_count": len(never_benched),
        "never_benched": never_benched[: args.max_new],
        "local_by_class": {k: len(v) for k, v in by_class.items()},
        "bench_results": bench,
        "errors": errors,
    }

    # only mark as seen what we actually reported
    for e in new_entries:
        ledger["seen"][e["key"]] = {"name": os.path.basename(e["path"]),
                                    "at": stamp, "how": "daily-review"}
    ledger["history"].append({"at": stamp, "new": len(new_entries),
                               "local": len(local)})
    ledger["history"] = ledger["history"][-90:]
    save_ledger(ledger, args.ledger)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)

    print(f"H3 LoRA daily review - {datetime.now().isoformat(timespec='seconds')}")
    print(f"   на диске: {len(local)} | в бенче: {len(bench)} | "
          f"новых (не видел раньше): {len(new_entries)}")
    print(f"   по классам: {gaps}")
    if errors:
        for e in errors:
            print(f"   ОШИБКА источника: {e}", file=sys.stderr)

    if new_entries:
        print("\nНОВЫЕ (раньше не показывались):")
        for e in new_entries[: args.max_new]:
            mark = "на диске" if e["on_disk"] else "скачать"
            u = e["usefulness"]
            print(f"   [{u['verdict']:6s} {u['score']:3d}] {e['path'][:78]}")
            print(f"           {u['category']} | {mark} | {u['notes'][0]}")
    else:
        print("\nНОВЫХ НЕТ - всё, что появилось, уже отмечено как просмотренное.")

    if never_benched:
        print("\nИМЕЮЩИЕСЯ, НО НЕ ПРОВЕРЕННЫЕ (в бенче не значились):")
        for e in never_benched[: 15]:
            u = e["usefulness"]
            print(f"   [{u['verdict']:6s} {u['score']:3d}] {e['name'][:72]}")
    print(f"\n   всего не проверено: {len(never_benched)}")
    print(f"   леджер: {args.ledger}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
