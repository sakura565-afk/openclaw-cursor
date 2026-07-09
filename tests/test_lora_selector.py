from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import lora_selector
from scripts.lora_selector import LoRASelector

PRESETS_PATH = lora_selector.DEFAULT_PRESETS_PATH


@pytest.fixture
def selector(tmp_path: Path) -> LoRASelector:
    return LoRASelector(presets_path=PRESETS_PATH, models_root=tmp_path)


# --------------------------------------------------------------------------- #
# Keyword detection / preset selection
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "prompt,expected",
    [
        ("naked blonde at the beach with dildo", "nsfw_explicit"),
        ("luxury sofa 3d render product photo", "furniture"),
        ("nude beach editorial blonde Koh Samui", "beach_safe"),
        ("naked woman on a bed", "nsfw_soft"),
        ("professional portrait headshot studio", "editorial"),
        ("animate this into a short video clip", "wan22_video"),
        ("face swap with the bfs head model", "face_swap"),
    ],
)
def test_select_picks_expected_preset(selector: LoRASelector, prompt: str, expected: str) -> None:
    assert selector.select(prompt).preset_id == expected


def test_explicit_has_klein_penetration_warning(selector: LoRASelector) -> None:
    preset = selector.select("naked blonde at the beach with dildo")
    assert preset.preset_id == "nsfw_explicit"
    assert preset.checkpoint == "klein"
    assert any("penetration" in w.lower() for w in preset.warnings)


def test_furniture_has_no_loras(selector: LoRASelector) -> None:
    preset = selector.select("luxury sofa 3d render product photo")
    assert preset.preset_id == "furniture"
    assert preset.loras == []


def test_auto_penetration_falls_back_to_pony(selector: LoRASelector) -> None:
    preset = selector.select("hardcore penetration scene", checkpoint="auto")
    assert preset.preset_id == "penetration_fallback"
    assert preset.checkpoint == "pony"


def test_explicit_model_override_klein_stays_klein(selector: LoRASelector) -> None:
    preset = selector.select("hardcore penetration scene", checkpoint="klein")
    assert preset.checkpoint == "klein"
    assert preset.preset_id == "nsfw_explicit"


def test_analyze_reports_matches_and_ranking(selector: LoRASelector) -> None:
    result = selector.analyze("naked woman with a dildo squatting")
    assert "dildo" in result.matched["explicit_toy"]
    assert "naked" in result.matched["nsfw"]
    assert result.poses["squat"]
    assert result.top_preset == "nsfw_explicit"
    # squat + toy is the forbidden THREE ARMS combination.
    assert any("THREE ARMS" in w for w in result.pose_warnings)


def test_analyze_default_when_no_keywords(selector: LoRASelector) -> None:
    result = selector.analyze("a quiet mountain landscape at dawn")
    assert result.top_preset == "editorial"


# --------------------------------------------------------------------------- #
# validate()
# --------------------------------------------------------------------------- #

def _touch(root: Path, rel: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"stub")


def test_validate_reports_missing_files(tmp_path: Path) -> None:
    # Only one of the three explicit LoRAs + the checkpoint are present.
    _touch(tmp_path, "unet/flux-2-klein-9b-Q4_0.gguf")
    _touch(tmp_path, "loras/flux2klein_nsfw.safetensors")
    selector = LoRASelector(presets_path=PRESETS_PATH, models_root=tmp_path)

    report = selector.validate("nsfw_explicit")

    assert report.ok is False
    assert len(report.missing) == 2
    assert any("NSFW_master.safetensors" in m for m in report.missing)
    assert any("aidmaNSFWunlock" in m for m in report.missing)


def test_validate_ok_when_all_present(tmp_path: Path) -> None:
    _touch(tmp_path, "unet/flux-2-klein-9b-Q4_0.gguf")
    _touch(tmp_path, "loras/flux2klein_nsfw.safetensors")
    _touch(tmp_path, "loras/NSFW_master.safetensors")
    _touch(tmp_path, "loras/aidmaNSFWunlock-FLUX-V0.2.safetensors")
    selector = LoRASelector(presets_path=PRESETS_PATH, models_root=tmp_path)

    report = selector.validate("nsfw_explicit")

    assert report.ok is True
    assert report.missing == []
    assert report.warnings  # keeps the empirical warnings


def test_validate_no_lora_preset(tmp_path: Path) -> None:
    _touch(tmp_path, "unet/flux-2-klein-9b-Q4_0.gguf")
    selector = LoRASelector(presets_path=PRESETS_PATH, models_root=tmp_path)

    report = selector.validate("furniture")

    assert report.ok is True
    assert report.missing == []


def test_validate_wan22_checkpoint_unpinned_warns(tmp_path: Path) -> None:
    _touch(tmp_path, "loras/wan22/Wan22_A14B_T2V_HIGH_Lightning_4steps_lora_250928_rank128_fp16.safetensors")
    _touch(tmp_path, "loras/wan22/Wan22_A14B_T2V_LOW_Lightning_4steps_lora_250928_rank64_fp16.safetensors")
    selector = LoRASelector(presets_path=PRESETS_PATH, models_root=tmp_path)

    report = selector.validate("wan22_video")

    assert report.ok is True
    assert any("not pinned" in w for w in report.warnings)


# --------------------------------------------------------------------------- #
# to_workflow_patch()
# --------------------------------------------------------------------------- #

def test_workflow_patch_klein_shape(selector: LoRASelector) -> None:
    preset = selector.get_preset("nsfw_explicit")
    patch = selector.to_workflow_patch(preset)

    assert patch["model"]["unet"] == "flux-2-klein-9b-Q4_0.gguf"
    assert patch["model"]["loras"][0] == {
        "path": "loras/flux2klein_nsfw.safetensors",
        "strength": 1.0,
    }
    assert patch["sampler"] == {
        "sampler": "euler",
        "scheduler": "simple",
        "steps": 24,
        "cfg": 1.0,
    }
    assert "clothes" in patch["negative"]


def test_workflow_patch_pony_uses_checkpoint_key(selector: LoRASelector) -> None:
    preset = selector.get_preset("penetration_fallback")
    patch = selector.to_workflow_patch(preset)

    assert patch["model"]["checkpoint"] == "ponyV6XL_v6StartWithThisOne.safetensors"
    assert patch["model"]["loras"] == []
    assert patch["sampler"]["sampler"] == "euler_ancestral"


# --------------------------------------------------------------------------- #
# End-to-end integration + CLI
# --------------------------------------------------------------------------- #

def test_end_to_end_analyze_select_patch(selector: LoRASelector) -> None:
    prompt = "naked blonde standing in waist-deep ocean water at the beach"
    analysis = selector.analyze(prompt)
    preset = selector.select(prompt)
    patch = selector.to_workflow_patch(preset)

    assert analysis.matched["beach"]
    assert preset.preset_id == "beach_safe"
    assert patch["model"]["loras"][0]["strength"] == 0.7
    assert patch["sampler"]["steps"] == 24


def test_list_presets_contains_all_eight(selector: LoRASelector) -> None:
    presets = selector.list_presets()
    assert len(presets) == 8
    assert set(presets) == {
        "nsfw_explicit",
        "nsfw_soft",
        "editorial",
        "furniture",
        "beach_safe",
        "penetration_fallback",
        "face_swap",
        "wan22_video",
    }


def test_cli_select_outputs_json(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    rc = lora_selector.main(
        [
            "naked blonde at the beach with dildo",
            "--presets", str(PRESETS_PATH),
            "--models-root", str(tmp_path),
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["preset_id"] == "nsfw_explicit"
    assert "workflow_patch" in payload


def test_cli_list_presets(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    rc = lora_selector.main(["--list-presets", "--presets", str(PRESETS_PATH)])
    out = capsys.readouterr().out
    assert rc == 0
    for pid in ("nsfw_explicit", "furniture", "wan22_video"):
        assert pid in out
