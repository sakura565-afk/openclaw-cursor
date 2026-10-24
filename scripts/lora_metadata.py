#!/usr/bin/env python3
"""Scan ComfyUI LoRA safetensors for trigger-word metadata and build prompt hints.

Verified pipeline behaviour (empirical, not guessed):

* A LoRA only fires when its trigger token appears **verbatim** in the positive prompt.
  Describing the same action in prose without the exact trigger leaves the effect off
  (e.g. clothing stays on).
* Some LoRAs require the trigger to be the **last token** of the prompt (often a token
  ending with a period, e.g. ``undressing.`` at the very end) at a given weight (e.g.
  0.85), together with a negative such as ``fabric not removed``.
* Tooling must encode those rules so operators append the correct suffix instead of
  paraphrasing the action.

Metadata is read from embedded safetensors ``__metadata__`` and/or a sidecar JSON file
``<stem>.json`` next to the ``.safetensors`` file.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

METADATA_KEY = "__metadata__"
MAX_HEADER_BYTES = 64 * 1024 * 1024

# Clothing-removal LoRAs use this negative addition in our stack.
CLOTHING_REMOVAL_NEGATIVE = "fabric not removed"

# Concepts that imply clothing-removal LoRAs (negative prompt + last-token trigger).
CLOTHING_REMOVAL_CONCEPTS: tuple[str, ...] = (
    "undressing",
    "undress",
    "nude",
    "naked",
    "nudity",
    "topless",
    "strip",
    "stripping",
    "clothing removal",
    "clothes off",
)

_CLOTHING_REMOVAL_RE = re.compile(
    r"(clothing\s+removal|clothes\s+off|remove\s+clothing|undress|undressing|"
    r"nude|naked|nudity|topless|strip(?:ping)?|strip\s+clothes|"
    r"fabric\s+not\s+removed|take\s+off\s+clothes)",
    re.IGNORECASE,
)

# Tokens stripped when deriving triggers from filenames / ss_output_name.
_NOISE_TOKENS: frozenset[str] = frozenset(
    {
        "minimax",
        "h3",
        "h3v",
        "ref2va",
        "fl2va",
        "fl2v",
        "flf",
        "i2v",
        "t2v",
        "lora",
        "loras",
        "lycoris",
        "sdxl",
        "sd15",
        "flux",
        "pony",
        "v",
        "v2",
        "v3",
        "str",
        "steps",
        "size",
        "px",
        "resolution",
        "trigger",
        "nsfw",
        "sfw",
        "h",
        "training",
        "epoch",
        "safetensors",
        "multiverse",
        "alcaitiff",
        "norris",
        "sanchez",
        "chuck",
        "table",
        "extended",
        "fast",
        "rev",
        "revision",
        "pro",
        "copy",
        "edition",
        "general",
        "dataset",
        "base",
        "screenx",
        "zshr",
        "dptv",
        "sec",
        "secs",
        "second",
        "seconds",
        "beta",
        "eros",
        "max",
        "use",
        "using",
        "video",
        "videos",
        "mvmt",
        "mmh3",
        "mm",
        "split",
        "alone",
        "attached",
        "person",
        "another",
        "people",
        "realism",
        "she",
        "her",
        "his",
        "him",
        "they",
        "them",
        "it",
        "its",
        "who",
        "what",
        "which",
        "with",
        "without",
        "from",
        "into",
        "onto",
        "over",
        "under",
        "when",
        "while",
        "that",
        "this",
        "then",
        "than",
        "very",
        "more",
        "most",
        "much",
        "many",
        "some",
        "such",
        "only",
        "also",
        "just",
        "like",
        "move",
        "moving",
        "moves",
        "slides",
        "has",
        "have",
        "wearing",
        "and",
        "the",
        "for",
        "rick",
        "collab",
        "pen",
        "minimaxh3",
        "ref",
        "va",
        "oac",
    }
)

_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_DIGIT_BOUNDARY_RE = re.compile(r"(?<=\d)(?=[A-Za-z])|(?<=[A-Za-z])(?=\d)")
_SEPARATOR_SPLIT_RE = re.compile(r"[\s_\-.,\(\)\[\]&+/!?+:]+")
_STRIP_EDGES_RE = re.compile(r"^[^a-zA-Z0-9]+|[^a-zA-Z0-9]+$")
_ALNUM_ONLY_RE = re.compile(r"[^a-zA-Z0-9]")
_WEIGHT_LIKE_RE = re.compile(
    r"^\d+(\.\d+)?$|^\d+(\.\d+)?str$|^\d+str$",
    re.IGNORECASE,
)
_SIZE_LIKE_RE = re.compile(r"^\d+x\d+$", re.IGNORECASE)
_DESCRIPTION_TRIGGERS_RE = re.compile(
    r"^\s*triggers?\s*:\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)
_TRIGGER_TAG_PREFIX = re.compile(r"^trigger:\s*(.+)$", re.IGNORECASE)


class SafetensorsMetadataError(ValueError):
    """Unreadable or truncated safetensors header."""


def read_safetensors_metadata(path: str | Path) -> tuple[dict[str, Any], int]:
    """Read ``__metadata__`` and count tensor entries without loading tensor data.

    Returns:
        A pair ``(metadata_dict, tensor_count)`` where ``tensor_count`` is the number
        of keys in the JSON header excluding ``__metadata__``.
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(f"LoRA file not found: {file_path}")

    file_size = file_path.stat().st_size
    if file_size < 8:
        raise SafetensorsMetadataError(f"{file_path.name}: file shorter than 8-byte header")

    with file_path.open("rb") as handle:
        header_length = int.from_bytes(handle.read(8), byteorder="little", signed=False)
        if header_length == 0 or header_length > MAX_HEADER_BYTES:
            raise SafetensorsMetadataError(f"{file_path.name}: invalid header length {header_length}")
        if header_length > file_size - 8:
            raise SafetensorsMetadataError(f"{file_path.name}: header length exceeds file size")
        raw_header = handle.read(header_length)

    if len(raw_header) != header_length:
        raise SafetensorsMetadataError(f"{file_path.name}: truncated header")

    try:
        header = json.loads(raw_header.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SafetensorsMetadataError(f"{file_path.name}: header is not JSON ({exc})") from exc

    if not isinstance(header, dict):
        raise SafetensorsMetadataError(f"{file_path.name}: header is not a JSON object")

    metadata = header.get(METADATA_KEY) or {}
    if not isinstance(metadata, dict):
        metadata = {}
    tensor_count = sum(1 for key in header if key != METADATA_KEY)
    structured = {
        "civitai_hash": _coerce_optional_str(metadata.get("sshs_model_hash")),
        "base_model_version": _coerce_optional_str(metadata.get("ss_base_model_version")),
    }
    return dict(metadata), tensor_count, structured


def _coerce_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def build_safetensors_fixture(header: dict[str, Any]) -> bytes:
    """Build a minimal safetensors file body (header only) for tests."""
    raw = json.dumps(header, ensure_ascii=False).encode("utf-8")
    return len(raw).to_bytes(8, byteorder="little", signed=False) + raw


def _split_trigger_tokens(raw: str) -> list[str]:
    parts = re.split(r"[,;\n]+", raw)
    out: list[str] = []
    for part in parts:
        token = part.strip().strip(",").strip()
        if token:
            out.append(token)
    return out


def _normalize_chunk_token(raw: str) -> str:
    """Lowercase alphanumeric-only form of a chunk (e.g. ``2Girls`` -> ``2girls``)."""
    return _ALNUM_ONLY_RE.sub("", raw).casefold()


def _strip_sign_and_check_numeric(token: str) -> str:
    """Strip leading ``+``/``-``; return token for further checks."""
    t = token.lstrip("+-")
    return t


def _is_junk_token(token: str) -> bool:
    """True if token should be dropped (noise, pure number, size, or weight)."""
    if not token:
        return True
    bare = _strip_sign_and_check_numeric(token)
    if not bare:
        return True
    low = bare.casefold()
    if low in _NOISE_TOKENS:
        return True
    if bare.isdigit():
        return True
    if _WEIGHT_LIKE_RE.match(bare):
        return True
    if _SIZE_LIKE_RE.match(bare):
        return True
    return False


def tokenize_lora_name(text: str) -> list[str]:
    """Split a LoRA filename stem into concept tokens (whole chunks before sub-tokens)."""
    if not text:
        return []
    raw_parts = _SEPARATOR_SPLIT_RE.split(text)
    wholes: list[str] = []
    subs: list[str] = []
    seen_wholes: set[str] = set()
    seen_subs: set[str] = set()

    for part in raw_parts:
        part = _STRIP_EDGES_RE.sub("", part.strip())
        if not part:
            continue
        whole = _normalize_chunk_token(part)
        if whole and not _is_junk_token(whole) and whole not in seen_wholes:
            seen_wholes.add(whole)
            wholes.append(whole)

        expanded = _CAMEL_BOUNDARY_RE.sub(" ", part)
        expanded = _DIGIT_BOUNDARY_RE.sub(" ", expanded)
        for piece in expanded.split():
            piece = _STRIP_EDGES_RE.sub("", piece.strip())
            if not piece:
                continue
            sub = _normalize_chunk_token(piece)
            if not sub or sub == whole:
                continue
            if _is_junk_token(sub):
                continue
            if sub in seen_wholes or sub in seen_subs:
                continue
            seen_subs.add(sub)
            subs.append(sub)

    return wholes + subs


def _is_noise_token(token: str) -> bool:
    return _is_junk_token(token)


def _extract_ss_tag_frequency_triggers(value: Any) -> list[str]:
    """Parse Kohya ``ss_tag_frequency`` keys ``<count>_<trigger>`` (highest explicit source)."""
    parsed: Any = None
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return []
    elif isinstance(value, dict):
        parsed = value
    else:
        return []

    if not isinstance(parsed, dict):
        return []

    ranked: list[tuple[int, str]] = []
    for key in parsed:
        if not isinstance(key, str):
            continue
        if "_" not in key:
            continue
        count_str, word = key.split("_", 1)
        try:
            count = int(count_str)
        except ValueError:
            continue
        word = word.strip()
        if word:
            ranked.append((count, word))

    ranked.sort(key=lambda pair: (-pair[0], pair[1].casefold()))
    out: list[str] = []
    seen: set[str] = set()
    for _count, word in ranked:
        key = word.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(word)
        if len(out) >= 3:
            break
    return out


def derive_trigger_words(record: dict[str, Any]) -> list[str]:
    """Guess trigger tokens: file stem first, embedded training names last."""
    embedded = record.get("_embedded") or {}
    file_name = record.get("name") or ""
    stem = Path(file_name).stem if file_name else ""

    label_fields: list[str] = []
    if stem:
        label_fields.append(stem)
    for key in ("ss_output_name", "name"):
        val = embedded.get(key)
        if isinstance(val, str) and val.strip():
            label_fields.append(val.strip())

    candidates: list[str] = []
    seen: set[str] = set()
    for label in label_fields:
        for token in tokenize_lora_name(label):
            if _is_noise_token(token):
                continue
            key = token.casefold()
            if key in seen:
                continue
            seen.add(key)
            candidates.append(token)
            if len(candidates) >= 3:
                return candidates
    return candidates


def must_be_last(record: dict[str, Any]) -> bool:
    """Whether the primary trigger must be the final prompt token."""
    trigger = _primary_trigger(record)
    embedded = record.get("_embedded") or {}
    sidecar = record.get("_sidecar") or {}
    for data in (embedded, sidecar, record):
        for key in ("must_be_last", "trigger_last", "trigger_must_be_last"):
            if key in data:
                val = data[key]
                if isinstance(val, str):
                    if val.lower() in {"1", "true", "yes", "on"}:
                        return True
                elif val:
                    return True
        desc = data.get("description")
        if isinstance(desc, str) and "must be last" in desc.casefold():
            return True

    if trigger.endswith("."):
        return True
    return _trigger_matches_clothing_removal(trigger)


def _trigger_matches_clothing_removal(trigger: str) -> bool:
    if not trigger:
        return False
    bare = trigger.rstrip(".").casefold()
    for concept in CLOTHING_REMOVAL_CONCEPTS:
        c = concept.casefold()
        if bare == c or bare.endswith(c) or bare.startswith(c + " ") or bare.startswith(c):
            return True
    return False


def _dedupe_triggers(tokens: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for token in tokens:
        key = token.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(token)
    return out


def _parse_ss_metadata_value(value: Any) -> list[str]:
    triggers: list[str] = []
    if value is None:
        return triggers
    if isinstance(value, dict):
        for key in ("trigger_words", "triggers", "tags"):
            if key in value:
                triggers.extend(_coerce_trigger_field(value[key]))
        for key, val in value.items():
            if isinstance(key, str) and "trigger" in key.lower():
                triggers.extend(_coerce_trigger_field(val))
        return triggers
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return triggers
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return _split_trigger_tokens(stripped)
        return _parse_ss_metadata_value(parsed)
    if isinstance(value, list):
        return _coerce_trigger_field(value)
    return triggers


def _coerce_trigger_field(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if isinstance(item, str):
                out.extend(_split_trigger_tokens(item))
        return out
    if isinstance(value, str):
        return _split_trigger_tokens(value)
    return []


def _extract_from_mapping(data: dict[str, Any]) -> tuple[list[str], str | None, str]:
    triggers: list[str] = []
    base_model: str | None = None
    description = ""

    for key in ("trigger_words", "triggers"):
        if key in data:
            triggers.extend(_coerce_trigger_field(data[key]))

    if "ss_metadata" in data:
        triggers.extend(_parse_ss_metadata_value(data["ss_metadata"]))

    tags = data.get("tags")
    if isinstance(tags, list):
        for tag in tags:
            if not isinstance(tag, str):
                continue
            m = _TRIGGER_TAG_PREFIX.match(tag.strip())
            if m:
                triggers.extend(_split_trigger_tokens(m.group(1)))

    desc = data.get("description")
    if isinstance(desc, str):
        description = desc.strip()
        for match in _DESCRIPTION_TRIGGERS_RE.finditer(desc):
            triggers.extend(_split_trigger_tokens(match.group(1)))

    for bm_key in ("base_model", "ss_base_model_version", "model"):
        if bm_key in data and data[bm_key]:
            base_model = str(data[bm_key]).strip()
            break

    return _dedupe_triggers(triggers), base_model, description


def _load_sidecar(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def normalize_record(path: str | Path) -> dict[str, Any]:
    """Combine embedded safetensors metadata with an optional ``<stem>.json`` sidecar."""
    file_path = Path(path).resolve()
    name = file_path.name
    embedded: dict[str, Any] = {}
    tensor_count = 0
    structured: dict[str, Any] = {}
    read_error: str | None = None
    try:
        embedded, tensor_count, structured = read_safetensors_metadata(file_path)
    except FileNotFoundError as exc:
        read_error = str(exc)
        embedded = {}
    except SafetensorsMetadataError as exc:
        read_error = str(exc)
        embedded = {}

    sidecar = _load_sidecar(file_path.with_suffix(".json"))

    emb_triggers, emb_base, emb_desc = _extract_from_mapping(embedded)
    side_triggers, side_base, side_desc = _extract_from_mapping(sidecar)
    ss_freq_triggers = _extract_ss_tag_frequency_triggers(embedded.get("ss_tag_frequency"))

    explicit_triggers = _dedupe_triggers(ss_freq_triggers + emb_triggers + side_triggers)
    base_model = side_base or emb_base or structured.get("base_model_version")
    description = side_desc or emb_desc
    civitai_hash = structured.get("civitai_hash")
    if not civitai_hash:
        civitai_hash = _coerce_optional_str(embedded.get("sshs_model_hash"))

    display_name = name
    for key in ("ss_output_name", "name"):
        val = embedded.get(key)
        if isinstance(val, str) and val.strip():
            display_name = val.strip()
            break

    emb_has = bool(embedded)
    side_has = bool(side_triggers or side_base or side_desc)
    if read_error:
        source = "none"
    elif not emb_has and not side_has:
        source = "none"
    elif emb_triggers:
        source = "safetensors"
    elif side_triggers:
        source = "sidecar"
    elif emb_has:
        source = "safetensors"
    else:
        source = "sidecar"

    record: dict[str, Any] = {
        "name": name,
        "display_name": display_name,
        "path": str(file_path),
        "trigger_words": explicit_triggers,
        "base_model": base_model,
        "description": description,
        "source": source,
        "tensor_count": tensor_count,
        "civitai_hash": civitai_hash,
        "_embedded": embedded,
        "_sidecar": sidecar,
    }
    if read_error:
        record["error"] = read_error

    if explicit_triggers:
        record["trigger_source"] = "explicit"
    elif read_error:
        record["trigger_source"] = "none"
    else:
        derived = derive_trigger_words(record)
        if derived:
            record["trigger_words"] = derived
            record["trigger_source"] = "derived"
        else:
            record["trigger_source"] = "none"

    record["must_be_last"] = must_be_last(record)
    return record


def scan_dir(directory: str | Path, recursive: bool = True) -> list[dict[str, Any]]:
    """Return normalized metadata records for every ``.safetensors`` file found."""
    root = Path(directory)
    if not root.is_dir():
        raise NotADirectoryError(f"Not a directory: {root}")
    pattern = "**/*.safetensors" if recursive else "*.safetensors"
    records = [normalize_record(path) for path in sorted(root.glob(pattern))]
    return records


def _escape_md_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def write_catalog(records: list[dict[str, Any]], out_path: str | Path, fmt: str = "md") -> None:
    """Write a markdown table or JSON catalogue."""
    out = Path(out_path)
    fmt_norm = fmt.lower()
    if fmt_norm == "json":
        payload = [
            {
                "name": r.get("display_name") or r["name"],
                "path": r["path"],
                "trigger_words": r.get("trigger_words") or [],
                "trigger_source": r.get("trigger_source") or "none",
                "must_be_last": bool(r.get("must_be_last")),
                "base_model": r.get("base_model"),
                "description": r.get("description") or "",
                "source": r.get("source") or "none",
                "civitai_hash": r.get("civitai_hash"),
                "error": r.get("error"),
            }
            for r in records
        ]
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return

    lines = [
        "| Name | Trigger words | Derived trigger (verify) | Trigger source | Must be last | Base model | Path |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for rec in records:
        trigger_source = rec.get("trigger_source") or "none"
        words = rec.get("trigger_words") or []
        if trigger_source == "derived":
            triggers_cell = "—"
            derived_cell = ", ".join(words) or "—"
        else:
            triggers_cell = ", ".join(words) or "—"
            derived_cell = "—"
        base = rec.get("base_model") or "—"
        must_last = "yes" if rec.get("must_be_last") else "no"
        label = rec.get("display_name") or rec["name"]
        lines.append(
            "| {name} | {triggers} | {derived} | {tsrc} | {last} | {base} | {path} |".format(
                name=_escape_md_cell(label),
                triggers=_escape_md_cell(triggers_cell),
                derived=_escape_md_cell(derived_cell),
                tsrc=_escape_md_cell(trigger_source),
                last=_escape_md_cell(must_last),
                base=_escape_md_cell(str(base)),
                path=_escape_md_cell(rec["path"]),
            )
        )
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _record_must_be_last(record: dict[str, Any], trigger: str) -> bool:
    if "must_be_last" in record:
        return bool(record["must_be_last"])
    return must_be_last(record)


def _primary_trigger(record: dict[str, Any]) -> str:
    words = record.get("trigger_words") or []
    return words[0] if words else ""


def build_prompt_suffix(record: dict[str, Any], weight: float = 0.85, prompt: str = "") -> str:
    """Return text to append so the trigger token is present (last when required).

    When the LoRA requires a last-token trigger, pass the current *prompt* so the
    suffix can be computed as everything that must be appended after stripping any
    trailing duplicate trigger; when *prompt* is empty, returns a leading-space suffix
    that ends with the trigger token.
    """
    trigger = _primary_trigger(record)
    if not trigger:
        return ""

    must_last = _record_must_be_last(record, trigger)
    weight_suffix = f" :{weight:g}" if weight is not None else ""

    if must_last and prompt:
        base = prompt.rstrip()
        low_base = base.casefold()
        low_trigger = trigger.casefold()
        if low_base.endswith(low_trigger):
            base = base[: len(base) - len(trigger)].rstrip()
        return f"{base} {trigger}{weight_suffix}"

    return f" {trigger}{weight_suffix}"


def _is_clothing_removal_record(record: dict[str, Any]) -> bool:
    embedded = record.get("_embedded") or {}
    label_bits = [
        record.get("name") or "",
        record.get("display_name") or "",
        record.get("description") or "",
    ]
    for key in ("name", "ss_output_name"):
        val = embedded.get(key)
        if isinstance(val, str):
            label_bits.append(val)
    label_blob = " ".join(label_bits)
    if _CLOTHING_REMOVAL_RE.search(label_blob):
        return True
    for token in derive_trigger_words(record) + (record.get("trigger_words") or []):
        if _trigger_matches_clothing_removal(token):
            return True
    return False


def build_negative(record: dict[str, Any]) -> str:
    """Suggested extra negative tokens (clothing-removal LoRAs only)."""
    if _is_clothing_removal_record(record):
        return CLOTHING_REMOVAL_NEGATIVE
    return ""


def catalog_index(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index records by basename and by normalized path string for lookup."""
    index: dict[str, dict[str, Any]] = {}
    for rec in records:
        index[rec["name"]] = rec
        index[Path(rec["path"]).name] = rec
        index[str(Path(rec["path"]))] = rec
        try:
            index[str(Path(rec["path"]).resolve())] = rec
        except OSError:
            pass
    return index


def lookup_by_civitai_hash(
    records: list[dict[str, Any]], model_hash: str
) -> dict[str, Any] | None:
    """Find a scanned record by CivitAI ``sshs_model_hash`` (case-insensitive)."""
    if not model_hash:
        return None
    target = model_hash.strip().casefold()
    for rec in records:
        h = rec.get("civitai_hash")
        if isinstance(h, str) and h.casefold() == target:
            return rec
    return None


def lookup_record(rel_or_abs_path: str, index: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """Find a scanned record for a preset-relative LoRA path."""
    if not index:
        return None
    candidate = Path(rel_or_abs_path)
    for key in (rel_or_abs_path, candidate.name, str(candidate)):
        if key in index:
            return index[key]
    return None


def enrich_loras(
    loras: list[dict[str, Any]],
    index: dict[str, dict[str, Any]],
    prompt: str = "",
) -> tuple[list[dict[str, Any]], list[str], str, str]:
    """Attach scanned trigger metadata and collect validation warnings.

    Returns:
        ``(enriched_loras, warnings, prompt_suffix, extra_negative)``
    """
    enriched: list[dict[str, Any]] = []
    warnings: list[str] = []
    suffix_parts: list[str] = []
    negatives: list[str] = []

    for lora in loras:
        entry = dict(lora)
        rec = lookup_record(str(lora.get("path", "")), index)
        if rec is None:
            enriched.append(entry)
            continue

        entry["trigger_words"] = list(rec.get("trigger_words") or [])
        entry["metadata_source"] = rec.get("source")
        entry["trigger_source"] = rec.get("trigger_source")
        entry["must_be_last"] = rec.get("must_be_last")
        if rec.get("base_model"):
            entry["base_model"] = rec.get("base_model")
        if rec.get("civitai_hash"):
            entry["civitai_hash"] = rec.get("civitai_hash")

        if not entry["trigger_words"]:
            warnings.append(f"Scanned LoRA has no trigger words: {lora.get('path')}")
        elif prompt:
            low = prompt.casefold()
            if not any(t in prompt or t.casefold() in low for t in entry["trigger_words"]):
                primary = entry["trigger_words"][0]
                warnings.append(
                    f"Prompt missing verbatim trigger for {lora.get('path')}: {primary!r}"
                )
                piece = build_prompt_suffix(rec, prompt=prompt)
                if piece and piece not in suffix_parts:
                    suffix_parts.append(piece)

        neg = build_negative(rec)
        if neg:
            negatives.append(neg)

        enriched.append(entry)

    # Prefer a single combined suffix (last-token rules may rewrite the whole prompt tail).
    prompt_suffix = suffix_parts[-1] if suffix_parts else ""
    extra_negative = ", ".join(_dedupe_triggers(negatives))
    return enriched, warnings, prompt_suffix, extra_negative


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lora_metadata",
        description="Scan LoRA safetensors metadata and write a trigger-word catalogue.",
    )
    parser.add_argument("--dir", required=True, help="Directory containing .safetensors LoRAs.")
    parser.add_argument("--out", required=True, help="Output catalogue path (.md or .json).")
    parser.add_argument("--json", action="store_true", help="Write JSON instead of markdown.")
    parser.add_argument(
        "--no-recursive",
        action="store_true",
        help="Only scan the top-level directory.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        records = scan_dir(args.dir, recursive=not args.no_recursive)
    except (NotADirectoryError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    fmt = "json" if args.json or str(args.out).lower().endswith(".json") else "md"
    write_catalog(records, args.out, fmt=fmt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
