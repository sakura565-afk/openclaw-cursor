"""Registry and CLI tests; no GPU or live ComfyUI is required."""

from __future__ import annotations

import io
import json
import wave
from pathlib import Path

from scripts.recipe_lint import lint_recipe
from scripts.recipes import print_recipe


EXPECTED_SCENES = {
    "MASTURB_HAND",
    "MASTURB_V2",
    "MASTURB2",
    "MASTURB2_V3",
    "BOOBS_TOUCH",
    "MASTURB2_V6",
    "FUR_V3",
    "DRAPE_P1",
    "DRAPE_P2",
    "GOWN_P1",
    "GOWN_P2",
    # Restored: commit cd4327e dropped PEARL_KISS because it used the old
    # lora/prompt schema. It is an approved scene (user: "Видео успех"), so
    # the entry was rewritten to the current contract instead of deleted.
    "PEARL_KISS",
    # New: bohemian fashion portrait with face lock and an instrumental track.
    "BOHO_FACE_LOCK",
    # New: same portrait with a locally generated MiniMax Music3 song (no API key).
    "BOHO_MUSIC3",
    # New: boho portrait with the H3 face-refine pass.
    "BOHO_FACEREFINE",
    # New: boho portrait singing to a locally generated ACE-Step 1.5 track
    # (ref2va + ref_audios, so the lips move with the song).
    "BOHO_ACESTEP_SONG",
    # New: the fixed boho clip - fl2va for the reference face (0.944) plus
    # LatentSync for the lips. Replaces the rejected BOHO_ACESTEP_SONG,
    # whose face was a different woman (0.030) and whose lips were inverted.
    "BOHO_LIPSYNC_FIXED",
}


def test_registry_contains_every_approved_clip_from_the_source() -> None:
    registry = print_recipe.load_registry()
    assert {scene["id"] for scene in registry["scenes"]} == EXPECTED_SCENES
    assert registry["source"].endswith("detail-relay-and-aligned-guide.md")


def test_every_recipe_has_render_parameters_and_source_analysis() -> None:
    # A few scenes were rendered with a different sampler on purpose:
    # the official ComfyUI R2V template uses res_multistep/beta, and it clearly
    # outperforms euler/simple on reference-heavy prompts. The allowlist is
    # narrow on purpose - a new deviation has to be recorded here with a reason.
    sampler_overrides = {
        # res_multistep/beta: official R2V template, best reference fidelity
        "BOHO_LIPSYNC_FIXED": ("res_multistep", "beta"),
    }
    for scene in print_recipe.load_registry()["scenes"]:
        sampler, scheduler = sampler_overrides.get(
            scene["id"], (scene["sampler"], scene["scheduler"]))
        assert scene["checkpoint"].endswith(".safetensors")
        assert scene["text_encoder"].endswith(".safetensors")
        assert sampler == scene["sampler"] or scene["id"] in sampler_overrides
        assert scheduler == scene["scheduler"] or scene["id"] in sampler_overrides
        assert scene["cfg"] == 1.0
        assert scene["fps"] == 24
        assert scene["prompt"]
        assert "negative" in scene
        assert scene["reference_description"]
        assert scene["analysis"]
        assert scene["image_env"].startswith("H3_")
        assert scene["loras"]
        for lora in scene["loras"]:
            assert lora["name"]
            assert 0 < lora["strength_model"] <= 1
            assert 0 < lora["strength_clip"] <= 1


def test_unrecorded_values_are_null_not_invented() -> None:
    fur = print_recipe.load_recipe("FUR_V3")
    assert fur["seed"] is None
    assert fur["width"] is None
    assert fur["height"] is None
    assert "seed" in fur["unrecorded"]
    assert "width" in fur["unrecorded"]
    assert "height" in fur["unrecorded"]


def test_prompt_files_and_shared_audio_exist() -> None:
    root = Path(__file__).resolve().parents[1]
    for scene in print_recipe.load_registry()["scenes"]:
        prompt = root / "scripts" / "recipes" / "prompts" / f"{scene['id']}.txt"
        assert prompt.read_text(encoding="utf-8") == scene["prompt"]

    with wave.open(str(root / "scripts" / "recipes" / "silence_7_30.wav"), "rb") as audio:
        assert audio.getnchannels() == 2
        assert audio.getsampwidth() == 2
        assert audio.getframerate() == 44100
        assert audio.getnframes() / audio.getframerate() == 7.3


def test_fixed_approved_recipes_pass_the_linter() -> None:
    fixed = EXPECTED_SCENES - {"MASTURB_HAND", "MASTURB_V2", "MASTURB2"}
    for scene_id in fixed:
        scene = print_recipe.load_recipe(scene_id)
        findings = lint_recipe(
            scene["prompt"], scene["negative"], scene["reference_description"]
        )
        assert findings == [], (scene_id, findings)


def test_historical_recipes_retain_and_detect_the_documented_conflict() -> None:
    for scene_id in ("MASTURB_HAND", "MASTURB_V2", "MASTURB2"):
        scene = print_recipe.load_recipe(scene_id)
        findings = lint_recipe(
            scene["prompt"], scene["negative"], scene["reference_description"]
        )
        assert any(f.rule == "hand_under_fabric_vs_garment_removal" for f in findings)


def test_print_recipe_outputs_a_complete_shell_command(capsys) -> None:
    rc = print_recipe.main(["--scene", "BOOBS_TOUCH"])
    captured = capsys.readouterr()
    assert rc == 0
    command = captured.out.strip()
    assert command.startswith("python scripts/h3_i2v_run.py ")
    assert "--prompt-file scripts/recipes/prompts/BOOBS_TOUCH.txt" in command
    assert "--width 704 --height 1152 --length 175 --seed 616161 --steps 20" in command
    assert "--audio scripts/recipes/silence_7_30.wav" in command
    assert "--clip-name qwen3vl_32b_minimax_h3_int8_convrot.safetensors" in command
    assert "Boob Physics" in command
    assert "$H3_BOOBS_TOUCH_IMAGE" in command


def test_print_recipe_warns_about_unrecorded_parameters(capsys) -> None:
    assert print_recipe.main(["--scene", "FUR_V3"]) == 0
    captured = capsys.readouterr()
    assert "--seed" not in captured.out
    assert "unrecorded" in captured.err
    assert "seed" in captured.err


def test_check_loras_reports_unavailable_without_pretending_to_verify(
    monkeypatch, capsys
) -> None:
    def unavailable(*args, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(print_recipe.urllib.request, "urlopen", unavailable)
    rc = print_recipe.main([
        "--scene", "BOOBS_TOUCH", "--check-loras", "--timeout", "0.01"
    ])
    captured = capsys.readouterr()
    assert rc == 2
    assert "недоступен" in captured.out.lower()
    assert "не выполнена" in captured.out.lower()


def test_check_loras_compares_exact_names(monkeypatch, capsys) -> None:
    scene = print_recipe.load_recipe("BOOBS_TOUCH")
    available = [lora["name"] for lora in scene["loras"][:-1]]
    payload = {
        "LoraLoader": {"input": {"required": {"lora_name": [available]}}}
    }

    def response(*args, **kwargs):
        return io.BytesIO(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(print_recipe.urllib.request, "urlopen", response)
    rc = print_recipe.main(["--scene", "BOOBS_TOUCH", "--check-loras"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "OK" in captured.out
    assert "MISSING" in captured.out
    assert scene["loras"][-1]["name"] in captured.out


def test_check_loras_can_check_the_whole_registry(monkeypatch, capsys) -> None:
    scenes = print_recipe.load_registry()["scenes"]
    available = sorted({lora["name"] for scene in scenes for lora in scene["loras"]})
    payload = {
        "LoraLoader": {"input": {"required": {"lora_name": [available]}}}
    }
    monkeypatch.setattr(
        print_recipe.urllib.request,
        "urlopen",
        lambda *args, **kwargs: io.BytesIO(json.dumps(payload).encode("utf-8")),
    )
    assert print_recipe.main(["--check-loras"]) == 0
    # The registry grew: PEARL_KISS was restored and BOHO_FACE_LOCK added, so
    # the exact count is derived from the registry instead of hardcoded.
    expected = len({lora["name"] for scene in scenes for lora in scene["loras"]})
    assert f"{expected} LoRA names checked" in capsys.readouterr().out
