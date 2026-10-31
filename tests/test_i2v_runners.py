"""Invariants of the I2V runners (H3 / FreeVideo / Qwen-Image-Edit).

Every test here pins a bug that cost a real render on 2026-10-31:

* ``LoraLoader`` exposes CLIP as output **1**; wiring ``[node, 0]`` makes ComfyUI
  answer HTTP 400 and the whole H3 run dies before a single frame.
* the stock ComfyUI Qwen autoencoder is a 3D/video VAE - the 2D VAE must be used.
* the FreeVideo runner must keep the render lock for the whole render, otherwise the
  local watchdog resurrects llama-server in the middle of it.
* LoRA names contain commas - they must never be split on a comma.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts import freevideo_lora_run as fv  # noqa: E402
from scripts import h3_i2v_run as h3  # noqa: E402
from scripts import qwen_edit_run as qwen  # noqa: E402


def _guide_id(graph) -> str:
    return next(k for k, v in graph.items() if v["class_type"] == "MiniMaxH3AddGuide")


def _h3node_id(graph) -> str:
    return next(k for k, v in graph.items() if v["class_type"] == "MiniMaxH3ImageToVideo")


def _h3_args(**overrides):
    args = argparse.Namespace(
        image="frame.jpg",
        prompt="a mermaid",
        prompt_prefix="",
        negative="",
        loras_json="[]",
        width=1056,
        height=704,
        length=175,
        seed=42,
        steps=14,
        prefix="test",
        last_frame="",
        guide_image="",
        guide_frame=0,
    )
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


class TestH3Workflow:
    @staticmethod
    def _by_type(graph, class_type):
        return {k: v for k, v in graph.items() if v["class_type"] == class_type}

    def test_clip_loader_output_is_zero(self) -> None:
        graph = h3.build_workflow(_h3_args())
        assert graph["3"]["class_type"] == "CLIPLoader"
        # the CLIPLoader's own CLIP is output 0
        h3_node = next(v for v in graph.values() if v["class_type"] == "MiniMaxH3ImageToVideo")
        assert h3_node["inputs"]["clip"] == ["3", 0]

    def test_first_lora_takes_clip_from_the_clip_loader(self) -> None:
        graph = h3.build_workflow(
            _h3_args(loras_json=json.dumps([{"n": "a.safetensors", "sm": 0.8, "sc": 0.6}]))
        )
        lora = next(iter(self._by_type(graph, "LoraLoader").values()))
        # CLIPLoader's CLIP is output 0
        assert lora["inputs"]["clip"] == ["3", 0]

    def test_chained_loras_take_clip_from_output_one(self) -> None:
        """The 400-error bug: a LoraLoader's CLIP is output 1, not 0."""
        graph = h3.build_workflow(
            _h3_args(
                loras_json=json.dumps(
                    [
                        {"n": "first.safetensors", "sm": 0.8, "sc": 0.6},
                        {"n": "second.safetensors", "sm": 0.6, "sc": 0.6},
                    ]
                )
            )
        )
        loras = self._by_type(graph, "LoraLoader")
        assert len(loras) == 2
        chained = [v for v in loras.values() if v["inputs"]["clip"][0] in loras]
        assert chained, "expected a LoRA chained onto another LoRA"
        for node in chained:
            assert node["inputs"]["clip"][1] == 1, (
                f"LoraLoader wired CLIP output {node['inputs']['clip'][1]}, expected 1"
            )

    def test_lora_name_with_comma_is_not_split(self) -> None:
        """A LoRA file name contains commas - splitting on them destroys the name."""
        name = "Penis Lora v1.1 - MinimaxH3 - PENISLORA,realistic.safetensors"
        graph = h3.build_workflow(_h3_args(loras_json=json.dumps([{"n": name, "sm": 0.8, "sc": 0.8}])))
        lora_id = next(iter(self._by_type(graph, "LoraLoader")))
        assert graph[lora_id]["inputs"]["lora_name"] == name

    def test_h3_uses_2d_video_vae_and_int8_weights(self) -> None:
        graph = h3.build_workflow(_h3_args())
        assert graph["4"]["inputs"]["vae_name"] == "minimax_h3_video_vae_fp16.safetensors"
        assert graph["2"]["inputs"]["unet_name"].endswith("int8_convrot.safetensors")

    def test_first_frame_feeds_the_video_node(self) -> None:
        graph = h3.build_workflow(_h3_args())
        node = next(v for v in graph.values() if v["class_type"] == "MiniMaxH3ImageToVideo")
        assert "first_frame" in node["inputs"]


class TestQwenEdit:
    def test_uses_the_2d_vae(self) -> None:
        """The stock Qwen autoencoder is a 3D VAE -> size mismatch on load."""
        assert qwen.VAE_NAME == "qwen_image_vae_2d.safetensors"

    def test_negative_prompt_blocks_people_and_deformation(self) -> None:
        neg = qwen.NEG.lower()
        for token in ("extra limbs", "fused bodies", "man sitting", "three heads"):
            assert token in neg


class TestFreeVideoRunner:
    def test_render_lock_path_is_configurable(self) -> None:
        assert fv.LOCK.name == "render_vram.lock"

    def test_lab_url_is_configurable(self) -> None:
        assert fv.BASE.startswith("http")

    def test_pause_llama_creates_the_render_lock(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(fv, "LOCK", tmp_path / "render_vram.lock")
        monkeypatch.setattr(fv, "llama_running", lambda: True)
        monkeypatch.setattr(fv.subprocess, "run", lambda *a, **k: None)
        assert fv.pause_llama() is True
        assert (tmp_path / "render_vram.lock").exists()

    def test_restore_llama_removes_the_lock(self, tmp_path, monkeypatch) -> None:
        lock = tmp_path / "render_vram.lock"
        lock.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(fv, "LOCK", lock)
        monkeypatch.setattr(fv, "LLAMA_CMD", tmp_path / "missing.json")
        fv.restore_llama(True)
        assert not lock.exists()


class TestH3NewCoreFeatures:
    """Features that only exist in the ComfyUI 2026-10 core."""

    def test_last_frame_is_wired_when_requested(self) -> None:
        graph = h3.build_workflow(_h3_args(last_frame="end.jpg"))
        node = next(v for v in graph.values() if v["class_type"] == "MiniMaxH3ImageToVideo")
        assert "last_frame" in node["inputs"]
        scale_id = node["inputs"]["last_frame"][0]
        assert graph[scale_id]["class_type"] == "ImageScaleToTotalPixels"
        loader = graph[scale_id]["inputs"]["image"][0]
        assert graph[loader]["inputs"]["image"] == "end.jpg"

    def test_no_last_frame_by_default(self) -> None:
        graph = h3.build_workflow(_h3_args())
        node = next(v for v in graph.values() if v["class_type"] == "MiniMaxH3ImageToVideo")
        assert "last_frame" not in node["inputs"]

    def test_guide_image_adds_a_guide_node_and_repoints_the_sampler(self) -> None:
        graph = h3.build_workflow(_h3_args(guide_image="ref.jpg", guide_frame=42))
        guide = next(v for v in graph.values() if v["class_type"] == "MiniMaxH3AddGuide")
        assert guide["inputs"]["frame_idx"] == 42
        sampler = next(v for v in graph.values() if v["class_type"] == "KSampler")
        # the sampler takes the guide conditioning but the ORIGINAL latent
        assert sampler["inputs"]["positive"] == [_guide_id(graph), 0]
        assert sampler["inputs"]["latent_image"] == [_h3node_id(graph), 1]

    def test_without_guide_the_sampler_uses_the_video_conditioning(self) -> None:
        graph = h3.build_workflow(_h3_args())
        sampler = next(v for v in graph.values() if v["class_type"] == "KSampler")
        assert sampler["inputs"]["positive"] == [_h3node_id(graph), 0]
