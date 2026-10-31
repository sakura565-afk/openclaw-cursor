"""Silent-fallback guards for video_pipeline.upscale.

A learned upscale (SeedVR2) that times out used to be replaced by a Lanczos
resize: the caller got a file with the right resolution and the audio intact,
but zero new detail - indistinguishable from a real upscale by looking at the
file. These tests pin the guards:

* ``UPSCALE_NO_FALLBACK=1`` makes the failure loud instead of silent;
* the poll timeout is configurable and defaults to a value that survives a
  slow (shared-GPU) run;
* the timeout message carries the prompt_id so the job can be grabbed manually.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest

from video_pipeline import upscale as up
from video_pipeline.config import UpscaleConfig


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UPSCALE_NO_FALLBACK", raising=False)
    monkeypatch.delenv("UPSCALE_POLL_TIMEOUT", raising=False)


class TestPollTimeout:
    def test_default_timeout_is_long_enough_for_a_slow_seedvr2_run(self) -> None:
        # 173 frames take ~20-25 min on a 16 GB box and up to ~2 h when the GPU
        # is shared; 3600 s timed out mid-run and silently fell back to Lanczos.
        assert up.POLL_TIMEOUT_SEC >= 4 * 3600

    def test_env_overrides_the_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("UPSCALE_POLL_TIMEOUT", "123")
        assert up._poll_timeout_sec() == 123.0

    def test_invalid_env_value_is_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("UPSCALE_POLL_TIMEOUT", "not-a-number")
        assert up._poll_timeout_sec() == float(up.POLL_TIMEOUT_SEC)

    def test_non_positive_env_value_is_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("UPSCALE_POLL_TIMEOUT", "0")
        assert up._poll_timeout_sec() == float(up.POLL_TIMEOUT_SEC)


class TestFallbackSwitch:
    def test_disabled_by_default(self) -> None:
        assert up._fallback_disabled() is False

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
    def test_enabled_by_env(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv("UPSCALE_NO_FALLBACK", value)
        assert up._fallback_disabled() is True

    def test_fallback_or_raise_raises_when_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("UPSCALE_NO_FALLBACK", "1")
        with pytest.raises(up.UpscaleError):
            up._fallback_or_raise(RuntimeError("boom"), backend="seedvr2", elapsed=10.0)

    def test_fallback_or_raise_returns_lanczos_when_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("UPSCALE_NO_FALLBACK", raising=False)
        assert up._fallback_or_raise(RuntimeError("boom"), backend="seedvr2") == "lanczos"


class TestTimeoutMessage:
    def test_timeout_message_carries_prompt_id_and_elapsed(self) -> None:
        class _Resp:
            status_code = 200

            @staticmethod
            def json() -> dict[str, object]:
                return {}

        with mock.patch.object(up, "_poll_timeout_sec", return_value=0.01), mock.patch.object(
            up.time, "sleep", lambda *_: None
        ):
            with pytest.raises(up.UpscaleError) as excinfo:
                up._poll_comfy_history("http://localhost:8188", "prompt-123", request_func=lambda *a, **k: _Resp())
        message = str(excinfo.value)
        assert "prompt-123" in message
        assert "timed out" in message


class TestUpscaleVideoFallback:
    """End-to-end guard: the learned backend failing must not be silent."""

    def _config(self) -> UpscaleConfig:
        return UpscaleConfig(backend="seedvr2", comfyui_url="http://localhost:8188")

    def test_timeout_raises_when_no_fallback_is_set(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("UPSCALE_NO_FALLBACK", "1")
        src = tmp_path / "in.mp4"
        src.write_bytes(b"fake")
        out = tmp_path / "out.mp4"

        def _boom(*args, **kwargs):
            raise up.UpscaleError("ComfyUI upscale timed out for prompt_id=abc123")

        with mock.patch.object(up, "fetch_object_info", return_value={"SeedVR2VideoUpscaler": {}}), \
             mock.patch.object(up, "_comfy_reachable", return_value=True), \
             mock.patch.object(up, "release_vram"), \
             mock.patch.object(up, "free_vram_gb", return_value=16.0), \
             mock.patch.object(up, "probe", return_value={"fps": 24.0, "has_audio": False}), \
             mock.patch.object(up, "_run_comfy_video_workflow", side_effect=_boom):
            with pytest.raises(up.UpscaleError) as excinfo:
                up.upscale_video(src, out, 1920, 1080, self._config())
        assert "abc123" in str(excinfo.value)

    def test_timeout_falls_back_to_lanczos_by_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("UPSCALE_NO_FALLBACK", raising=False)
        src = tmp_path / "in.mp4"
        src.write_bytes(b"fake")
        out = tmp_path / "out.mp4"

        def _boom(*args, **kwargs):
            raise up.UpscaleError("ComfyUI upscale timed out for prompt_id=abc123")

        with mock.patch.object(up, "fetch_object_info", return_value={"SeedVR2VideoUpscaler": {}}), \
             mock.patch.object(up, "_comfy_reachable", return_value=True), \
             mock.patch.object(up, "release_vram"), \
             mock.patch.object(up, "free_vram_gb", return_value=16.0), \
             mock.patch.object(up, "probe", return_value={"fps": 24.0, "has_audio": False}), \
             mock.patch.object(up, "_run_comfy_video_workflow", side_effect=_boom), \
             mock.patch.object(up, "lanczos_upscale", return_value=out):
            assert up.upscale_video(src, out, 1920, 1080, self._config()) == "lanczos"
