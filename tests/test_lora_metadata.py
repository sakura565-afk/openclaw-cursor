from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.lora_metadata import (
    build_negative,
    build_prompt_suffix,
    build_safetensors_fixture,
    normalize_record,
    read_safetensors_metadata,
    scan_dir,
    write_catalog,
)

METADATA_KEY = "__metadata__"


def _write_lora(path: Path, header: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_safetensors_fixture(header))
    return path


def test_read_safetensors_metadata_parses_header_and_counts_tensors(tmp_path: Path) -> None:
    header = {
        METADATA_KEY: {"description": "demo", "trigger_words": ["alpha"]},
        "tensor.a": {"dtype": "F16", "shape": [1], "data_offsets": [0, 2]},
        "tensor.b": {"dtype": "F16", "shape": [1], "data_offsets": [2, 4]},
    }
    path = _write_lora(tmp_path / "tiny.safetensors", header)

    metadata, tensor_count = read_safetensors_metadata(path)

    assert metadata["description"] == "demo"
    assert metadata["trigger_words"] == ["alpha"]
    assert tensor_count == 2


@pytest.mark.parametrize(
    "header,expected_triggers,expected_source",
    [
        (
            {METADATA_KEY: {"trigger_words": ["foo", "bar"]}},
            ["foo", "bar"],
            "safetensors",
        ),
        (
            {
                METADATA_KEY: {
                    "ss_metadata": json.dumps({"trigger_words": ["ss_one", "ss_two"]}),
                }
            },
            ["ss_one", "ss_two"],
            "safetensors",
        ),
        (
            {METADATA_KEY: {"tags": ["style:photo", "trigger: undressing.", "other"]}},
            ["undressing."],
            "safetensors",
        ),
        (
            {
                METADATA_KEY: {
                    "description": "LoRA for fabric\nTriggers: silk, sheer",
                }
            },
            ["silk", "sheer"],
            "safetensors",
        ),
    ],
)
def test_normalize_record_trigger_sources(
    tmp_path: Path,
    header: dict,
    expected_triggers: list[str],
    expected_source: str,
) -> None:
    path = _write_lora(tmp_path / "lora.safetensors", {**header, "t0": {}})
    record = normalize_record(path)
    assert record["trigger_words"] == expected_triggers
    assert record["source"] == expected_source


def test_normalize_record_merges_sidecar(tmp_path: Path) -> None:
    path = _write_lora(tmp_path / "merged.safetensors", {METADATA_KEY: {}, "w": {}})
    sidecar = path.with_suffix(".json")
    sidecar.write_text(json.dumps({"triggers": ["from_sidecar"]}), encoding="utf-8")

    record = normalize_record(path)

    assert record["trigger_words"] == ["from_sidecar"]
    assert record["source"] == "sidecar"


def test_build_prompt_suffix_must_be_last_rewrites_prompt() -> None:
    record = {
        "trigger_words": ["undressing."],
        "_embedded": {"must_be_last": True},
        "_sidecar": {},
    }
    suffix = build_prompt_suffix(record, weight=0.85, prompt="woman in a summer dress")
    assert suffix.endswith("undressing. :0.85")
    assert suffix.split()[-2] == "undressing."


def test_build_prompt_suffix_period_trigger_implies_last() -> None:
    record = {"trigger_words": ["undressing."], "_embedded": {}, "_sidecar": {}}
    piece = build_prompt_suffix(record, weight=0.85)
    assert piece.strip() == "undressing. :0.85"


def test_build_negative_clothing_removal() -> None:
    record = {
        "name": "undressing-PN.safetensors",
        "description": "clothing removal effect",
        "_embedded": {},
        "_sidecar": {},
    }
    assert build_negative(record) == "fabric not removed"


def test_build_negative_other_lora() -> None:
    record = {"name": "style.safetensors", "description": "cinematic grade", "_embedded": {}, "_sidecar": {}}
    assert build_negative(record) == ""


def test_write_catalog_markdown_one_row_per_record(tmp_path: Path) -> None:
    records = [
        {
            "name": "a.safetensors",
            "path": "/loras/a.safetensors",
            "trigger_words": ["t1"],
            "base_model": "flux",
            "description": "",
            "source": "safetensors",
        },
        {
            "name": "b.safetensors",
            "path": "/loras/b.safetensors",
            "trigger_words": [],
            "base_model": None,
            "description": "",
            "source": "none",
        },
    ]
    out = tmp_path / "LORA_CATALOG.md"
    write_catalog(records, out, fmt="md")
    text = out.read_text(encoding="utf-8")
    assert text.count("| a.safetensors |") == 1
    assert text.count("| b.safetensors |") == 1
    assert "| Name | Trigger words |" in text


def test_scan_dir_finds_nested_safetensors(tmp_path: Path) -> None:
    _write_lora(
        tmp_path / "nested" / "child.safetensors",
        {METADATA_KEY: {"triggers": ["kid"]}, "w": {}},
    )
    records = scan_dir(tmp_path)
    assert len(records) == 1
    assert records[0]["trigger_words"] == ["kid"]
