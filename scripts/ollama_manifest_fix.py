#!/usr/bin/env python3
"""
Repair Ollama on-disk manifests so current servers can list and load local models.

Background (Ollama open-source layout, unchanged through recent releases):

- Installed models are discovered from files matching::

    models/manifests/*/*/*/*

  That is exactly four path segments after ``manifests/`` (host, namespace,
  model name, tag filename). Anything else is invisible to ``ollama list``.

- Each file must be JSON matching Docker distribution manifest v2 schema 2::

    {"schemaVersion":2,"mediaType":"application/vnd.docker.distribution.manifest.v2+json",
     "config":{...},"layers":[...]}

- Layer and config ``digest`` values must look like ``sha256:<64 hex>``; on disk,
  blobs are stored as ``models/blobs/sha256-<64 hex>``.

- ``size`` must match the blob file size or later validation can fail and
  ``pull`` may ignore existing blobs.

After moving ``OLLAMA_MODELS`` (e.g. to ``H:\\ollama\\models``), older trees
sometimes use a different registry host directory (e.g. ``registry.ollama.com``)
or slightly inconsistent digests/sizes. This tool writes manifests under the
canonical host path and normalizes digest + size fields.

Permanent mitigation:

1. Set ``OLLAMA_MODELS`` to the directory that contains both ``blobs`` and
   ``manifests`` (System environment on Windows, or service user).
2. Keep manifests under ``registry.ollama.ai/library/...`` unless you use a
   custom registry explicitly in model names.
3. Restart the Ollama service after fixing files.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Iterable

# Canonical defaults in github.com/ollama/ollama types/model/name.go
DEFAULT_REGISTRY = os.environ.get("OLLAMA_DEFAULT_REGISTRY", "registry.ollama.ai")
DEFAULT_NAMESPACE = "library"

DOCKER_MANIFEST_V2 = "application/vnd.docker.distribution.manifest.v2+json"

# Hostnames that should be mirrored to DEFAULT_REGISTRY so glob-based discovery finds them.
REGISTRY_ALIASES: dict[str, str] = {
    "registry.ollama.com": DEFAULT_REGISTRY,
    "registry.ollama.cloud": DEFAULT_REGISTRY,
    # Add more known legacy host keys if needed (lowercase).
}


def _models_root(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit.expanduser().resolve()
    env = os.environ.get("OLLAMA_MODELS", "").strip().strip('"').strip("'")
    if env:
        return Path(env).expanduser().resolve()
    home = Path.home()
    return (home / ".ollama" / "models").resolve()


def manifest_paths_under(root: Path) -> list[Path]:
    """Match Ollama's manifest.Manifests glob: manifests/*/*/*/* (files only)."""
    manifests = root / "manifests"
    if not manifests.is_dir():
        return []
    out: list[Path] = []
    for host in manifests.iterdir():
        if not host.is_dir():
            continue
        for ns in host.iterdir():
            if not ns.is_dir():
                continue
            for model in ns.iterdir():
                if not model.is_dir():
                    continue
                for tag in model.iterdir():
                    if tag.is_file():
                        out.append(tag)
    return sorted(out)


def parse_manifest_rel_path(manifest_file: Path, manifests_dir: Path) -> tuple[str, str, str, str] | None:
    try:
        rel = manifest_file.resolve().relative_to(manifests_dir.resolve())
    except ValueError:
        return None
    parts = rel.parts
    if len(parts) != 4:
        return None
    return parts[0], parts[1], parts[2], parts[3]


def canonicalize_digest(digest: str) -> str | None:
    if not digest or not isinstance(digest, str):
        return None
    d = digest.strip()
    if not d:
        return None
    low = d.lower()
    if low.startswith("sha256:"):
        hexpart = low[7:]
        if len(hexpart) == 64 and all(c in "0123456789abcdef" for c in hexpart):
            return f"sha256:{hexpart}"
        return None
    if low.startswith("sha256-"):
        hexpart = low[7:]
        if len(hexpart) == 64 and all(c in "0123456789abcdef" for c in hexpart):
            return f"sha256:{hexpart}"
        return None
    # bare 64 hex (seen in some broken exports)
    if len(low) == 64 and all(c in "0123456789abcdef" for c in low):
        return f"sha256:{low}"
    return None


def blob_path_for_digest(models_root: Path, digest: str) -> Path:
    d = canonicalize_digest(digest)
    if not d:
        raise ValueError(f"invalid digest: {digest!r}")
    filename = d.replace(":", "-", 1)
    return models_root / "blobs" / filename


def _stat_size(path: Path) -> int:
    return path.stat().st_size


def fix_manifest_obj(data: dict[str, Any], models_root: Path) -> tuple[dict[str, Any], list[str]]:
    """
    Normalize config + layers digests and sizes against files under models_root/blobs.
    Returns (new_manifest_dict, warnings).
    """
    warnings: list[str] = []

    if not isinstance(data, dict):
        raise ValueError("manifest root must be a JSON object")

    if data.get("schemaVersion") != 2:
        warnings.append(f"unexpected schemaVersion {data.get('schemaVersion')!r}, still attempting repair")

    if data.get("mediaType") != DOCKER_MANIFEST_V2:
        old = data.get("mediaType")
        if old is not None:
            warnings.append(f"mediaType was {old!r}, setting to {DOCKER_MANIFEST_V2!r}")
    data["schemaVersion"] = 2
    data["mediaType"] = DOCKER_MANIFEST_V2

    def repair_layer(kind: str, layer: dict[str, Any]) -> None:
        if not isinstance(layer, dict):
            return
        mediatype = layer.get("mediaType", "")
        digest = layer.get("digest")
        canon = canonicalize_digest(digest) if isinstance(digest, str) else None
        if not canon:
            if mediatype == "application/vnd.ollama.image.tensor":
                warnings.append(f"{kind}: tensor layer without usable digest skipped")
            else:
                warnings.append(f"{kind}: missing or invalid digest, left unchanged ({digest!r})")
            return
        layer["digest"] = canon
        try:
            bp = blob_path_for_digest(models_root, canon)
        except ValueError:
            warnings.append(f"{kind}: digest not canonicalized ({digest!r})")
            return
        if not bp.is_file():
            warnings.append(f"{kind}: blob missing for {canon} ({bp})")
            return
        sz = _stat_size(bp)
        layer["size"] = sz

    cfg = data.get("config")
    if isinstance(cfg, dict):
        repair_layer("config", cfg)

    layers = data.get("layers")
    if isinstance(layers, list):
        for idx, layer in enumerate(layers):
            if isinstance(layer, dict):
                repair_layer(f"layers[{idx}]", layer)
            else:
                warnings.append(f"layers[{idx}] is not an object")

    return data, warnings


def go_style_json_lines(obj: dict[str, Any]) -> bytes:
    """Match encoding/json.Encoder.Encode: one JSON object + trailing newline."""
    # Go's encoder escapes HTML by default; Ollama manifests don't need '<' etc.
    text = json.dumps(obj, separators=(",", ":"), ensure_ascii=False) + "\n"
    return text.encode("utf-8")


def process_manifest_file(
    path: Path,
    models_root: Path,
    *,
    dry_run: bool,
    repair: bool,
    mirror_aliases: bool,
) -> dict[str, Any]:
    """Returns a report dict for one file."""
    manifests_dir = models_root / "manifests"
    parsed = parse_manifest_rel_path(path, manifests_dir)
    report: dict[str, Any] = {"path": str(path), "actions": [], "warnings": [], "errors": []}
    if not parsed:
        report["errors"].append("not a 4-part manifest path; skipped")
        return report

    host, namespace, model, tag = parsed
    actions: list[str] = []
    scan_path = path.resolve()
    canon_host = REGISTRY_ALIASES.get(host.lower(), host) if mirror_aliases else host
    alias_to_canonical = (
        mirror_aliases and canon_host != host and REGISTRY_ALIASES.get(host.lower()) is not None
    )
    canon_path = (manifests_dir / canon_host / namespace / model / tag).resolve()

    if repair:
        try:
            raw = path.read_bytes()
            data = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            report["errors"].append(f"could not read/parse manifest: {exc}")
            return report

        if not isinstance(data, dict):
            report["errors"].append("manifest JSON is not an object")
            return report

        try:
            fixed, warns = fix_manifest_obj(data, models_root)
        except ValueError as exc:
            report["errors"].append(str(exc))
            return report
        report["warnings"].extend(warns)
        new_bytes = go_style_json_lines(fixed)

        targets: list[Path] = [scan_path]
        if alias_to_canonical:
            targets.insert(0, canon_path)
        unique_targets = sorted({t for t in targets}, key=lambda p: str(p))

        change_bits: list[str] = []
        if new_bytes != raw:
            change_bits.append("digest/size/mediaType")
        if alias_to_canonical:
            change_bits.append(f"mirror->{canon_host}")
        if change_bits:
            actions.append("update: " + ", ".join(change_bits))

        if not dry_run:
            for dest in unique_targets:
                dest.parent.mkdir(parents=True, exist_ok=True)
                tmp = dest.with_suffix(dest.suffix + ".tmp")
                tmp.write_bytes(new_bytes)
                tmp.replace(dest)
    elif alias_to_canonical:
        actions.append(f"copy unmodified manifest to canonical host {canon_host} (also use default repair)")
        if not dry_run:
            shutil.copy2(path, canon_path)

    report["actions"] = actions
    return report


def run_fix(
    models_root: Path,
    *,
    dry_run: bool,
    repair: bool,
    mirror_aliases: bool,
    paths: Iterable[Path] | None,
) -> int:
    manifests_dir = models_root / "manifests"
    blobs_dir = models_root / "blobs"
    if not blobs_dir.is_dir():
        print(f"error: blobs directory missing: {blobs_dir}", file=sys.stderr)
        return 2
    if not manifests_dir.is_dir():
        print(f"error: manifests directory missing: {manifests_dir}", file=sys.stderr)
        return 2

    candidates = list(paths) if paths is not None else manifest_paths_under(models_root)
    if not candidates:
        print(f"no manifest files found under {manifests_dir}")
        return 1

    exit_code = 0
    for mf in sorted({p.resolve() for p in candidates}):
        if not mf.is_file():
            print(f"skip (not a file): {mf}", file=sys.stderr)
            exit_code = 1
            continue
        rep = process_manifest_file(
            mf,
            models_root,
            dry_run=dry_run,
            repair=repair,
            mirror_aliases=mirror_aliases,
        )
        if rep["errors"]:
            exit_code = 1
        line = mf.name if mf.parent.name else str(mf)
        parent_parts = mf.parent.parts
        if len(parent_parts) >= 4:
            line = f"{Path(*parent_parts[-3:]).as_posix()}/{mf.name}"
        status = []
        if rep["actions"]:
            status.append("; ".join(rep["actions"]))
        else:
            status.append("no changes")
        if rep["warnings"]:
            status.append("warnings: " + "; ".join(rep["warnings"]))
        if rep["errors"]:
            status.append("ERR: " + "; ".join(rep["errors"]))
        print(f"[{'dry-run ' if dry_run else ''}{line}] {' | '.join(status)}")
    return exit_code


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Fix Ollama local manifest layout (discoverability + digest/size consistency).",
    )
    p.add_argument(
        "--models",
        type=Path,
        default=None,
        help="Models directory (defaults to OLLAMA_MODELS or ~/.ollama/models).",
    )
    p.add_argument(
        "manifest_paths",
        nargs="*",
        type=Path,
        help="Optional specific manifest files to fix; default: all manifests under manifests/*/*/*/*.",
    )
    p.add_argument("--dry-run", action="store_true", help="Print actions without writing.")
    p.add_argument(
        "--no-repair-json",
        action="store_true",
        help="Only mirror registry aliases; do not rewrite manifest JSON.",
    )
    p.add_argument(
        "--no-mirror",
        action="store_true",
        help="Do not write duplicate manifests under the canonical registry.ollama.ai host.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    root = _models_root(args.models)
    return run_fix(
        root,
        dry_run=args.dry_run,
        repair=not args.no_repair_json,
        mirror_aliases=not args.no_mirror,
        paths=args.manifest_paths if args.manifest_paths else None,
    )


if __name__ == "__main__":
    raise SystemExit(main())
