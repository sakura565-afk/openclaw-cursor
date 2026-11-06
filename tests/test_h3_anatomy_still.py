from __future__ import annotations

import argparse
import json
import wave
from pathlib import Path

import pytest

from scripts import h3_i2v_run as h3
from scripts.anatomy_still.h3_still import FPS, _silent_audio
from scripts.anatomy_still.pipeline import AnatomyStillPipeline


def _args(**overrides):
    values = dict(
        image=None,
        still_reference="ready_still.png",
        prompt="preserve the reference frame",
        prompt_prefix="",
        recipe_prompt="",
        recipes_file="",
        negative="",
        loras_json="[]",
        width=736,
        height=992,
        length=81,
        seed=42,
        steps=14,
        prefix="test",
        last_frame="",
        guide_image="",
        guide_frame=0,
        ref_images=[],
        ref_image=[],
        ref_image_size="match",
        turbo=False,
        clip_name="encoder.safetensors",
    )
    values.update(overrides)
    return argparse.Namespace(**values)


def test_h3_workflow_uses_ready_still_as_first_frame() -> None:
    args = h3.apply_anatomy_options(_args(recipe_prompt="chef"))
    graph = h3.build_workflow(args)
    assert graph["1"]["inputs"]["image"] == "ready_still.png"
    h3_node = next(v for v in graph.values() if v["class_type"] == "MiniMaxH3ImageToVideo")
    assert h3_node["inputs"]["first_frame"]
    assert "exact visible anatomy" in h3_node["inputs"]["prompt"]
    assert "vulva" not in h3_node["inputs"]["prompt"].lower()


def test_h3_image_remains_backward_compatible_without_still() -> None:
    args = h3.apply_anatomy_options(_args(image="source.jpg", still_reference=""))
    graph = h3.build_workflow(args)
    assert graph["1"]["inputs"]["image"] == "source.jpg"


def test_h3_rejects_missing_both_image_and_still() -> None:
    try:
        h3.apply_anatomy_options(_args(still_reference=""))
    except ValueError as exc:
        assert "still-reference" in str(exc)
    else:
        raise AssertionError("missing source image was accepted")


def test_pipeline_dry_run_never_calls_gpu_runners(tmp_path: Path) -> None:
    calls: list[str] = []

    def forbidden(*args, **kwargs):
        calls.append("GPU")
        raise AssertionError("GPU runner called during dry-run")

    pipeline = AnatomyStillPipeline(
        "chef", edit_runner=forbidden, refine_runner=forbidden
    )
    report = pipeline.run(tmp_path / "source.jpg", tmp_path / "out", dry_run=True)
    assert not calls
    assert report["status"] == "dry_run_unverified"
    assert report["passes"][0]["kind"] == "edit"
    # the chef scene renders with H3 and stops there: a Qwen refine after an
    # H3 edit can only censor what H3 produced, so the plan has one pass
    assert report["passes"][0]["backend"] == "h3_still"
    assert len(report["passes"]) == 1
    assert report["passes"][0]["denoise"] < 0.55
    assert not (tmp_path / "out" / "render_started").exists()


def test_pipeline_stops_after_validator_accepts_masked_refinement(tmp_path: Path) -> None:
    source = tmp_path / "source.jpg"
    source.write_bytes(b"source")
    edited = tmp_path / "edited.png"
    edited.write_bytes(b"edited")
    refined = tmp_path / "refined.png"
    refined.write_bytes(b"refined")
    mask_path = tmp_path / "mask.png"
    mask_path.write_bytes(b"mask")
    calls: list[tuple[str, float]] = []

    class Result:
        def __init__(self, visibility: float, passed: bool):
            self.visibility = visibility
            self.passed = passed

        def to_dict(self):
            return {"visibility": self.visibility, "passed": self.passed}

    def edit(*args, **kwargs):
        calls.append(("edit", kwargs["denoise"]))
        return edited

    def refine(*args, **kwargs):
        calls.append(("refine", kwargs["denoise"]))
        return refined

    validations = iter([Result(0.20, False), Result(0.72, True)])
    pipeline = AnatomyStillPipeline(
        # chef_qwen keeps the masked refine passes; the chef scene renders
        # with H3 and stops after the edit
        "chef_qwen",
        edit_runner=edit,
        refine_runner=refine,
        mask_builder=lambda image, recipe: mask_path,
        validator=lambda image, mask: next(validations),
    )
    report = pipeline.run(source, tmp_path / "out")
    assert report["status"] == "passed"
    assert report["visibility"] == 0.72
    assert [name for name, _ in calls] == ["edit", "refine"]
    assert report["still"].endswith("refined.png")


def test_silent_placeholder_audio_is_as_long_as_the_requested_clip(
    tmp_path: Path,
) -> None:
    """H3 takes the clip length from the audio.

    A one-second placeholder produced a 21-frame render regardless of the
    length that was asked for, so the placeholder has to match the clip.
    """
    wav = _silent_audio(tmp_path / "silent.wav", seconds=175 / float(FPS))
    with wave.open(str(wav), "rb") as handle:
        frames = handle.getnframes()
        rate = handle.getframerate()
    assert frames / rate == pytest.approx(175 / float(FPS), abs=0.01)

    src = (Path(__file__).resolve().parents[1] / "scripts" / "anatomy_still"
           / "h3_still.py").read_text(encoding="utf-8")
    assert "seconds=length / float(FPS)" in src
