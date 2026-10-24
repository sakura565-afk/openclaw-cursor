from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.lora_metadata import (
    build_negative,
    build_prompt_suffix,
    build_safetensors_fixture,
    derive_trigger_words,
    lookup_by_civitai_hash,
    must_be_last,
    normalize_record,
    read_safetensors_metadata,
    scan_dir,
    tokenize_lora_name,
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

    metadata, tensor_count, structured = read_safetensors_metadata(path)

    assert metadata["description"] == "demo"
    assert metadata["trigger_words"] == ["alpha"]
    assert tensor_count == 2
    assert structured["civitai_hash"] is None
    assert structured["base_model_version"] is None


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
    assert "| Name | Trigger words | Derived trigger (verify) |" in text


def test_read_safetensors_metadata_exposes_h3_fields(tmp_path: Path) -> None:
    header = {
        METADATA_KEY: {
            "sshs_model_hash": "abc123hash",
            "ss_base_model_version": "minimax_h3_ref2va",
            "name": "MinimaxH3-Blowjob-512",
            "ss_output_name": "MinimaxH3-Blowjob-512",
        },
        "w": {},
    }
    path = _write_lora(tmp_path / "Blowjob_NSFW.safetensors", header)
    _, _, structured = read_safetensors_metadata(path)
    assert structured["civitai_hash"] == "abc123hash"
    assert structured["base_model_version"] == "minimax_h3_ref2va"


def test_h3_lora_derives_blowjob_trigger(tmp_path: Path) -> None:
    header = {
        METADATA_KEY: {
            "ss_base_model_version": "minimax_h3_ref2va",
            "name": "MinimaxH3-Blowjob-512",
            "ss_output_name": "MinimaxH3-Blowjob-512",
        },
        "w": {},
    }
    path = _write_lora(tmp_path / "Blowjob_NSFW.safetensors", header)
    record = normalize_record(path)
    assert record["trigger_source"] == "derived"
    assert "blowjob" in record["trigger_words"]
    assert "minimax" not in record["trigger_words"]
    assert "h3" not in record["trigger_words"]
    assert "512" not in record["trigger_words"]


def test_derive_trigger_words_tokenization_examples() -> None:
    record = {
        "name": "BoobSlider.safetensors",
        "_embedded": {"ss_output_name": "BoobSlider"},
    }
    derived = derive_trigger_words(record)
    assert "boob" in derived
    assert "slider" in derived

    tokens = tokenize_lora_name("BJ_Sandwich_v2.0")
    assert "bj" in tokens
    assert "sandwich" in tokens

    noise_record = {
        "name": "x.safetensors",
        "_embedded": {"name": "minimax-h3-0.8str-512"},
    }
    derived_noise = derive_trigger_words(noise_record)
    for bad in ("minimax", "h3", "512", "0.8", "str"):
        assert bad not in derived_noise


def test_must_be_last_rules() -> None:
    period_rec = {"trigger_words": ["undressing."], "_embedded": {}, "_sidecar": {}}
    assert must_be_last(period_rec) is True

    undress_rec = {
        "trigger_words": ["undressing"],
        "name": "undressing.safetensors",
        "_embedded": {},
        "_sidecar": {},
    }
    assert must_be_last(undress_rec) is True

    plain = {"trigger_words": ["blowjob"], "_embedded": {}, "_sidecar": {}}
    assert must_be_last(plain) is False


def test_normalize_record_corrupt_header_does_not_raise(tmp_path: Path) -> None:
    bad = tmp_path / "broken.safetensors"
    bad.write_bytes(b"\x00\x00\x00\x00\x00\x00\x00\x00")
    record = normalize_record(bad)
    assert record["source"] == "none"
    assert "error" in record
    assert record["trigger_source"] == "none"


def test_lookup_by_civitai_hash() -> None:
    records = [
        {"name": "a.safetensors", "civitai_hash": "DeadBeef"},
        {"name": "b.safetensors", "civitai_hash": "other"},
    ]
    found = lookup_by_civitai_hash(records, "deadbeef")
    assert found is not None
    assert found["name"] == "a.safetensors"


def test_write_catalog_markdown_has_new_columns(tmp_path: Path) -> None:
    records = [
        {
            "name": "a.safetensors",
            "display_name": "a",
            "path": "/loras/a.safetensors",
            "trigger_words": ["t1"],
            "trigger_source": "explicit",
            "must_be_last": False,
            "base_model": "flux",
            "description": "",
            "source": "safetensors",
        },
    ]
    out = tmp_path / "LORA_CATALOG.md"
    write_catalog(records, out, fmt="md")
    text = out.read_text(encoding="utf-8")
    assert "| Trigger source |" in text
    assert "| Must be last |" in text
    assert "| Derived trigger (verify) |" in text


def test_scan_dir_finds_nested_safetensors(tmp_path: Path) -> None:
    _write_lora(
        tmp_path / "nested" / "child.safetensors",
        {METADATA_KEY: {"triggers": ["kid"]}, "w": {}},
    )
    records = scan_dir(tmp_path)
    assert len(records) == 1
    assert records[0]["trigger_words"] == ["kid"]


def test_ss_tag_frequency_explicit_trigger(tmp_path: Path) -> None:
    freq = json.dumps({"10_undressing": {"undressing": 121, "1girl": 120}})
    header = {METADATA_KEY: {"ss_tag_frequency": freq}, "w": {}}
    path = _write_lora(tmp_path / "undressing-PN.safetensors", header)
    record = normalize_record(path)
    assert record["trigger_source"] == "explicit"
    assert record["trigger_words"] == ["undressing"]
    assert record["must_be_last"] is True
    assert build_negative(record) == "fabric not removed"


def test_ss_tag_frequency_trigger_with_spaces(tmp_path: Path) -> None:
    freq = json.dumps(
        {"2_undressing woman": {"undressing a woman in a white lingerie posing for a picture": 1}}
    )
    header = {METADATA_KEY: {"ss_tag_frequency": freq}, "w": {}}
    path = _write_lora(tmp_path / "undressing-PN_ggrue_565222.safetensors", header)
    record = normalize_record(path)
    assert record["trigger_words"] == ["undressing woman"]
    assert record["trigger_source"] == "explicit"
    assert record["must_be_last"] is True
    suffix = build_prompt_suffix(record, weight=0.85, prompt="woman in dress")
    assert suffix.endswith("undressing woman :0.85")


def test_derive_priority_filename_over_training_names(tmp_path: Path) -> None:
    header = {
        METADATA_KEY: {
            "name": "epoch_1",
            "ss_output_name": "training_4958664-20260919165509962",
        },
        "w": {},
    }
    path = _write_lora(tmp_path / "2Girls_2Handed_HJ.safetensors", header)
    record = normalize_record(path)
    assert record["trigger_source"] == "derived"
    assert record["trigger_words"][0] == "2girls"
    for bad in ("training", "epoch", "safetensors"):
        assert bad not in record["trigger_words"]


def test_derived_never_emits_training_run_noise(tmp_path: Path) -> None:
    header = {
        METADATA_KEY: {
            "name": "Rick Sanchez x Alcaitiff x Chuck Norris: multiverse",
            "ss_output_name": "rick_x_alcaitiff_x_chuck_collab_v9000",
        },
        "w": {},
    }
    path = _write_lora(tmp_path / "Detail Slider - MinimaxH3.safetensors", header)
    record = normalize_record(path)
    derived = record["trigger_words"]
    for bad in ("training", "epoch", "safetensors", "norris", "multiverse", "alcaitiff"):
        assert bad not in derived
    assert "detail" in derived
    assert "slider" in derived


def test_comma_list_splits_into_separate_tokens() -> None:
    tokens = tokenize_lora_name("jiggle,grab,massage,bounce")
    assert tokens[:4] == ["jiggle", "grab", "massage", "bounce"]


def test_tokenization_drops_noise_fragments() -> None:
    tokens = tokenize_lora_name("Ass jiggle (I2V) - FL2VA & I2V - MinimaxH3 - 0.8-1str")
    assert "(i" not in tokens
    assert "i2v" not in tokens
    assert "0.8" not in tokens
    assert "1str" not in tokens
    assert "jiggle" in tokens

    motion_tokens = tokenize_lora_name("Better Motion - 720x1280,15-30steps,0.2-0.4str")
    assert "1280,15" not in motion_tokens
    assert "steps,0" not in motion_tokens
    assert "1280" not in motion_tokens
    assert "720x1280" not in motion_tokens

    plus_tokens = tokenize_lora_name("+8 -8")
    assert "8" not in plus_tokens
    assert "+8" not in plus_tokens

    cumouf = tokenize_lora_name("CUMOUF,0.4str,use eros max beta6,10seconds videos")
    assert "cumouf" in cumouf
    assert "seconds" not in cumouf
    assert "cumouf,0" not in cumouf


def test_chunk_preferred_over_subtoken() -> None:
    tokens = tokenize_lora_name("2Girls_2Handed")
    assert tokens[0] == "2girls"
    assert "girls" in tokens
    assert tokens.index("2girls") < tokens.index("girls")
