"""Offline MiniMax H3 LoRA catalog: filename metadata, classification and JSON cache.

No weights are opened and no network is used. Manifest facts override filename hints.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Iterable

DEFAULT_ROOTS = ("H3_LORA_ROOT_C", "H3_LORA_ROOT_G")
_SETTING_RE = re.compile(
    r"^(?:[-+]?\d+(?:\.\d+)?(?:\s*[-+]\s*[-+]?\d+(?:\.\d+)?)?str|"
    r"\d+x\d+|\d+(?:-\d+)?steps?|(?:euler|resmultistep|res_multistep|ddim_uniform)\+?"
    r"(?:simple|beta|normal)?|simple|beta|normal|cfg\d+(?:\.\d+)?|turbo.*|stage\d+|"
    r"res_\d+s\+.*|seeds?\d*\+.*|shift\d+(?:\.\d+)?|t2v.*|i2v.*|fl2va.*|ref2va.*|r2v.*)$",
    re.IGNORECASE,
)
_STRENGTH_RANGE_RE = re.compile(
    r"(?<![\w.])([-+]?\d+(?:\.\d+)?)\s*(?:-|\.\.|\s+(?=\+))\s*([-+]?\d+(?:\.\d+)?)\s*str\b",
    re.IGNORECASE,
)
_STRENGTH_RE = re.compile(r"(?<![\w.])([-+]?\d+(?:\.\d+)?)\s*str\b", re.IGNORECASE)


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def parse_strength_range(filename: str) -> list[float] | None:
    match = _STRENGTH_RANGE_RE.search(filename)
    if match:
        return [float(match.group(1)), float(match.group(2))]
    match = _STRENGTH_RE.search(filename)
    return [float(match.group(1)), float(match.group(1))] if match else None


def parse_filename_metadata(filename: str) -> dict:
    stem = Path(filename).stem
    hint = re.split(r"\s+-\s+(?:minimax\s*h3|minimaxh3|mmh3|mm-h3)\s+-\s+", stem, flags=re.I)
    tail = hint[-1] if len(hint) > 1 else ""
    words = []
    for token in tail.split(","):
        token = token.strip(" .")
        if token and not _SETTING_RE.match(token) and not re.match(
            r"^(?:[-+]?\d+(?:\.\d+)?(?:\s*[-+]\s*[-+]?\d+(?:\.\d+)?)?str\b|\d+x\d+|\d+(?:-\d+)?steps?\b)",
            token,
            re.IGNORECASE,
        ):
            words.append(token)
    return {
        "trigger_words": words,
        "trigger_source": "filename" if words else "unknown",
        "strength_range": parse_strength_range(stem),
    }


def classify_lora(filename: str) -> dict:
    value = filename.lower()
    rules = [
        ("detail", r"micro.?details|sharpness|restore.?enhance|intricate.?composition"),
        ("realism", r"realism|ph0t0r34l|photorealistic|perfect.?eyes|perfect.?skin"),
        ("face", r"natural.?face|face.?swap|character.?swap|ahegao|speech"),
        ("anatomy", r"male.?anatomy|pussy|hminnie|vagina|anus|penis"),
        ("camera", r"handheld.?camera|camera.?motion|locked.?stabilization|telephoto|drone.?shot|crossview|orb360|\bpov\b"),
        ("motion", r"motion|physics|shaking|shaky|slow.?and.?smooth|slowsmooth|edge.?time|frozen|time.?freeze|speed.?slider|breast.?drop|bouncing.?boobs|heavy.?breasts"),
        ("utility", r"finalcut|vfx.?edit|parasyte|continuity.?repair|enhance.?improve"),
        ("nsfw", r"nsfw|fingering|masturbation|jerking|dildo|side.?splits|beanflk|blowjob|cumshot|cumfacial|\bsex\b|fuck|cowgirl|doggy|rimjob|handjob|titfuck|paizuri|orgasm|twerk|spank|nipple|lingerie|panties|thong|cameltoe|groping|scissoring|tribbing|pegging|vibrator|squirt|deepthroat|oral|footjob|hogtie|undressing|nude|\bbj\b"),
    ]
    classes = [name for name, pattern in rules if re.search(pattern, value)]
    if not classes:
        classes = ["utility"]
    # Primary category follows the most specific useful benchmark asset.
    priority = ["anatomy", "camera", "face", "detail", "realism", "motion", "nsfw", "utility"]
    category = next(name for name in priority if name in classes)
    return {"category": category, "classes": classes}


def load_manifest(path: str | Path | None = None) -> dict:
    manifest_path = Path(path) if path else Path(__file__).with_name("h3_lora_manifest.json")
    if not manifest_path.exists():
        return {"loras": {}}
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def match_manifest(filename: str, manifest: dict) -> tuple[str | None, dict | None]:
    stem = Path(filename).stem
    normalized = normalize_name(stem)
    best: tuple[int, str, dict] | None = None
    for key, entry in manifest.get("loras", {}).items():
        for alias in entry.get("aliases", [key]):
            alias_norm = normalize_name(alias)
            if not alias_norm:
                continue
            if normalized == alias_norm or normalized.startswith(alias_norm):
                candidate = (len(alias_norm), key, entry)
                if best is None or candidate[0] > best[0]:
                    best = candidate
    return (best[1], best[2]) if best else (None, None)


def scan_loras(roots: Iterable[str | Path], *, manifest: dict | None = None, include_non_h3: bool = False) -> list[dict]:
    manifest = manifest or {"loras": {}}
    rows: list[dict] = []
    for root_value in roots:
        root = Path(root_value)
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.safetensors")):
            key, entry = match_manifest(path.name, manifest)
            probable_h3 = bool(entry) or bool(re.search(r"minimax.?h3|\bmm.?h3\b|\bh3\b", path.stem, re.I))
            if not probable_h3 and not include_non_h3:
                continue
            parsed = parse_filename_metadata(path.name)
            classified = classify_lora(path.name)
            if entry:
                classified = {"category": entry["category"], "classes": entry.get("classes", [entry["category"]])}
                if entry.get("trigger_words"):
                    parsed["trigger_words"] = entry["trigger_words"]
                    parsed["trigger_source"] = "manifest"
                if entry.get("recommended_strength") is not None:
                    parsed["recommended_strength"] = float(entry["recommended_strength"])
            parsed.setdefault("recommended_strength", None)
            rows.append({
                "filename": path.name,
                "comfy_name": path.relative_to(root).as_posix(),
                "path": str(path),
                "root": str(root),
                "exists": path.is_file(),
                "size_bytes": path.stat().st_size,
                "size_mib": round(path.stat().st_size / (1024 * 1024), 3),
                "probable_h3": probable_h3,
                "manifest_id": key,
                **classified,
                **parsed,
            })
    rows.sort(key=lambda row: (row["category"], row["filename"].lower(), row["path"]))
    return rows


def write_catalog(rows: list[dict], output: str | Path) -> Path:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": 1, "count": len(rows), "loras": rows}
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return path


def format_table(rows: list[dict]) -> str:
    headers = ("CATEGORY", "MIB", "STRENGTH", "TRIGGERS", "FILE")
    table = []
    for row in rows:
        strength = row.get("strength_range")
        strength_text = "-".join(f"{value:g}" for value in strength) if strength else "?"
        table.append((row["category"], f"{row['size_mib']:.1f}", strength_text,
                      ",".join(row["trigger_words"])[:58] or "?", row["comfy_name"]))
    widths = [max(len(headers[i]), *(len(row[i]) for row in table)) if table else len(headers[i]) for i in range(len(headers))]
    lines = ["  ".join(headers[i].ljust(widths[i]) for i in range(len(headers)))]
    lines.append("  ".join("-" * width for width in widths))
    lines.extend("  ".join(row[i].ljust(widths[i]) for i in range(len(headers))) for row in table)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", action="append", default=[], help="LoRA root; repeatable")
    parser.add_argument("--manifest", default=str(Path(__file__).with_name("h3_lora_manifest.json")))
    parser.add_argument("--output", default=str(Path(tempfile.gettempdir()) / "h3_lora_catalog.json"))
    parser.add_argument("--category", choices=["realism", "detail", "motion", "face", "anatomy", "camera", "nsfw", "utility"])
    parser.add_argument("--include-non-h3", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of a table")
    args = parser.parse_args(argv)
    roots = args.root or [os.environ[name] for name in DEFAULT_ROOTS if os.environ.get(name)]
    if not roots:
        parser.error("pass --root or set H3_LORA_ROOT_C / H3_LORA_ROOT_G")
    rows = scan_loras(roots, manifest=load_manifest(args.manifest), include_non_h3=args.include_non_h3)
    if args.category:
        rows = [row for row in rows if row["category"] == args.category]
    write_catalog(rows, args.output)
    print(json.dumps({"count": len(rows), "loras": rows}, ensure_ascii=False, indent=2) if args.json else format_table(rows))
    print(f"\nJSON cache: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
