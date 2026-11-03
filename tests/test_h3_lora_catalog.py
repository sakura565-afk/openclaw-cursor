from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import h3_lora_catalog as catalog


def test_parse_filename_metadata_extracts_trigger_and_strength() -> None:
    parsed = catalog.parse_filename_metadata(
        "Photorealistic Image Generator - MinimaxH3 - ph0t0r34l,0.25-1str.safetensors"
    )

    assert parsed["trigger_words"] == ["ph0t0r34l"]
    assert parsed["strength_range"] == [0.25, 1.0]


def test_parse_filename_metadata_discards_sampling_recipe() -> None:
    parsed = catalog.parse_filename_metadata(
        "Example - MinimaxH3 - trigger_one,720x1280,15-30steps,euler+simple,0.8str.safetensors"
    )

    assert parsed["trigger_words"] == ["trigger_one"]
    assert parsed["strength_range"] == [0.8, 0.8]


def test_parse_signed_slider_range() -> None:
    parsed = catalog.parse_filename_metadata("Motion-Speed Slider - MinimaxH3 - -8 +8str.safetensors")
    assert parsed["strength_range"] == [-8.0, 8.0]


@pytest.mark.parametrize(
    ("filename", "category"),
    [
        ("Add Micro Details - MinimaxH3 - addmicrodetails.safetensors", "detail"),
        ("Spatial and Physics - MinimaxH3.safetensors", "motion"),
        ("Natural Face and Speech - MinimaxH3.safetensors", "face"),
        ("HMInnie - MinimaxH3 - inniepussy.safetensors", "anatomy"),
        ("Drone Shot - MinimaxH3.safetensors", "camera"),
        ("HMNSFW v2.5 - MinimaxH3.safetensors", "nsfw"),
        ("FinalCut MiniMaxH3.safetensors", "utility"),
        ("Realism Slider - MinimaxH3.safetensors", "realism"),
    ],
)
def test_classify(filename: str, category: str) -> None:
    assert catalog.classify_lora(filename)["category"] == category


def test_scan_catalog_matches_manifest_and_writes_cache(tmp_path: Path) -> None:
    root = tmp_path / "loras"
    root.mkdir()
    (root / "Photorealistic_Image_Generator.safetensors").write_bytes(b"12345")
    (root / "unrelated_qwen_edit.safetensors").write_bytes(b"123")
    manifest = {
        "loras": {
            "photorealistic": {
                "aliases": ["Photorealistic Image Generator", "Photorealistic_Image_Generator"],
                "category": "realism",
                "trigger_words": ["ph0t0r34l"],
                "recommended_strength": 0.75,
            }
        }
    }

    rows = catalog.scan_loras([root], manifest=manifest)
    output = tmp_path / "catalog.json"
    catalog.write_catalog(rows, output)

    assert len(rows) == 1
    assert rows[0]["trigger_words"] == ["ph0t0r34l"]
    assert rows[0]["recommended_strength"] == 0.75
    assert rows[0]["size_bytes"] == 5
    assert rows[0]["comfy_name"] == "Photorealistic_Image_Generator.safetensors"
    assert json.loads(output.read_text(encoding="utf-8"))["count"] == 1


def test_repository_manifest_is_complete_and_valid() -> None:
    path = Path(__file__).parents[1] / "scripts" / "h3_lora_manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))

    assert len(data["loras"]) >= 45
    required = {"realism", "detail", "motion", "face", "anatomy", "camera", "nsfw", "utility"}
    assert required <= set(data["asset_classes"])
    for key, entry in data["loras"].items():
        assert entry["category"] in required, key
        assert entry["expected_effect"], key
        assert entry["asset_class"] in data["asset_classes"], key
        assert 0 < abs(float(entry["recommended_strength"])) <= 10, key
        assert entry["sources"], key
