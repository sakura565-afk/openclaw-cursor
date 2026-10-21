"""Тесты базы знаний H3 и префлайт-валидатора."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts import h3_preflight
from src.h3_knowledge import constants as C
from src.h3_knowledge.lora_compat import (
    H3_BASE_MODEL,
    SafetensorsError,
    build_safetensors_header_bytes,
    check_lora_compatibility,
    check_lora_compatibility_from_header,
    list_loras_in_dir,
    read_safetensors_header,
)

H3_HEADER: dict[str, Any] = {
    "__metadata__": {"format": "pt"},
    "diffusion_model.blocks.0.attn.to_q.lora_A.weight": {
        "dtype": "F16", "shape": [16, 2048], "data_offsets": [0, 0],
    },
    "diffusion_model.blocks.0.attn.to_q.lora_B.weight": {
        "dtype": "F16", "shape": [2048, 16], "data_offsets": [0, 0],
    },
    "diffusion_model.blocks.1.ff.net.lora_A.weight": {
        "dtype": "F16", "shape": [16, 2048], "data_offsets": [0, 0],
    },
}

FLUX_HEADER: dict[str, Any] = {
    "__metadata__": {"format": "pt", "modelspec.architecture": "flux-1-dev/lora"},
    "transformer.single_transformer_blocks.0.attn.to_q.lora_A.weight": {
        "dtype": "F16", "shape": [16, 3072], "data_offsets": [0, 0],
    },
    "transformer.single_transformer_blocks.0.attn.to_q.lora_B.weight": {
        "dtype": "F16", "shape": [3072, 16], "data_offsets": [0, 0],
    },
}

SDXL_HEADER: dict[str, Any] = {
    "lora_te_text_model_encoder_layers_0_mlp_fc1.lora_down.weight": {
        "dtype": "F16", "shape": [8, 768], "data_offsets": [0, 0],
    },
    "lora_te_text_model_encoder_layers_0_mlp_fc1.lora_up.weight": {
        "dtype": "F16", "shape": [768, 8], "data_offsets": [0, 0],
    },
}

NSGIRL = "NSGIRL-MiniMax-H3-LoRA-By-MM744.safetensors"
FLUX_LORA = "nsfw_flux_lora_v1.safetensors"
SOURCE_AR_896_1280 = 896 / 1280


def write_lora(path: Path, header: dict[str, Any]) -> Path:
    """Записать синтетический safetensors с заданным заголовком."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_safetensors_header_bytes(header))
    return path


@pytest.fixture
def lora_dir(tmp_path: Path) -> Path:
    """Каталог с H3-совместимой и Flux-несовместимой LoRA."""
    directory = tmp_path / "loras"
    write_lora(directory / NSGIRL, H3_HEADER)
    write_lora(directory / FLUX_LORA, FLUX_HEADER)
    return directory


def valid_workflow() -> dict[str, Any]:
    """Эталонный валидный workflow (896x1280, length 175, H3-LoRA с триггером)."""
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": C.BASE_MODEL}},
        "2": {
            "class_type": "LoraLoader",
            "inputs": {
                "lora_name": NSGIRL,
                "strength_model": 0.85,
                "strength_clip": 1.0,
                "model": ["1", 0],
            },
        },
        "3": {
            "class_type": "MiniMaxH3ImageToVideo",
            "inputs": {
                "prompt": "nsgirl, photorealistic skin, static hero framing",
                "negative_prompt": "blurry, low quality",
                "width": 896,
                "height": 1280,
                "length": 175,
                "shift_video": C.DEFAULT_SHIFT_VIDEO,
                "shift_audio": C.DEFAULT_SHIFT_AUDIO,
            },
        },
    }


def write_workflow(path: Path, workflow: dict[str, Any]) -> Path:
    path.write_text(json.dumps(workflow, ensure_ascii=False), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Константы: сетка длин и разрешений
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("length", C.VALID_LENGTHS)
def test_valid_lengths_pass(length: int) -> None:
    assert C.is_valid_length(length)


@pytest.mark.parametrize("length", [79, 100, 200])
def test_invalid_lengths_rejected(length: int) -> None:
    assert not C.is_valid_length(length)


def test_is_valid_length_rejects_non_int() -> None:
    assert not C.is_valid_length("175")
    assert not C.is_valid_length(175.0)
    assert not C.is_valid_length(None)


@pytest.mark.parametrize(
    "length,expected",
    [(79, 80), (100, 113), (200, 175), (160, 145), (1, 80)],
)
def test_nearest_valid_length(length: int, expected: int) -> None:
    assert C.nearest_valid_length(length) == expected


def test_length_175_is_729_seconds() -> None:
    assert C.length_to_seconds(175) == 7.29


@pytest.mark.parametrize(
    "width,height,expected_ar",
    [
        (768, 1152, 0.6667),
        (896, 1280, 0.7000),
        (1024, 1216, 0.8421),
        (832, 1184, 0.7027),
        (640, 1024, 0.6250),
    ],
)
def test_resolution_grid_aspect_ratios(width: int, height: int, expected_ar: float) -> None:
    resolution = C.find_resolution(width, height)
    assert resolution is not None, f"{width}x{height} отсутствует в сетке"
    assert resolution.aspect_ratio == pytest.approx(width / height)
    assert round(resolution.aspect_ratio, 4) == expected_ar


def test_find_resolution_outside_grid() -> None:
    assert C.find_resolution(900, 1300) is None


def test_resolutions_for_ar_uses_tolerance() -> None:
    """AR 0.7 покрывают два разрешения сетки: 896x1280 и 832x1184 (расхождение 0.0027)."""
    matching = C.resolutions_for_ar(SOURCE_AR_896_1280)
    assert {(res.width, res.height) for res in matching} == {(896, 1280), (832, 1184)}
    assert C.resolutions_for_ar(0.8421052631578947) == [C.find_resolution(1024, 1216)]
    assert C.resolutions_for_ar(0.5) == []
    assert C.AR_TOLERANCE == 0.005


def test_known_loras_catalogue() -> None:
    nsgirl = C.KNOWN_LORAS[NSGIRL]
    assert nsgirl["trigger"] == "nsgirl"
    assert nsgirl["strength_range"] == (0.85, 0.9)
    assert "h3-realism-people-t2v-i2v-r2v.safetensors" in nsgirl["avoid_with"]

    realism = C.KNOWN_LORAS["h3-realism-people-t2v-i2v-r2v.safetensors"]
    assert NSGIRL in realism["avoid_with"]

    facial = C.KNOWN_LORAS["minimax-h3-facial-realism-closeup-cp2000.safetensors"]
    assert facial["strength_model"] == 0.45


# --------------------------------------------------------------------------- #
# Чтение заголовка safetensors
# --------------------------------------------------------------------------- #


def test_read_safetensors_header_parses_json(tmp_path: Path) -> None:
    path = write_lora(tmp_path / "tiny.safetensors", H3_HEADER)
    header = read_safetensors_header(path)
    assert header == H3_HEADER
    assert header["__metadata__"]["format"] == "pt"


def test_read_safetensors_header_reads_only_header(tmp_path: Path) -> None:
    """Веса за заголовком не читаются и не мешают разбору."""
    path = tmp_path / "with_body.safetensors"
    path.write_bytes(build_safetensors_header_bytes(H3_HEADER) + b"\x00" * 4096)
    assert read_safetensors_header(path) == H3_HEADER


def test_read_safetensors_header_rejects_garbage(tmp_path: Path) -> None:
    path = tmp_path / "garbage.safetensors"
    path.write_bytes(b"not a safetensors file at all")
    with pytest.raises(SafetensorsError):
        read_safetensors_header(path)


def test_read_safetensors_header_rejects_truncated(tmp_path: Path) -> None:
    path = tmp_path / "short.safetensors"
    path.write_bytes(b"\x01\x02")
    with pytest.raises(SafetensorsError):
        read_safetensors_header(path)


def test_read_safetensors_header_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_safetensors_header(tmp_path / "nope.safetensors")


# --------------------------------------------------------------------------- #
# Совместимость LoRA
# --------------------------------------------------------------------------- #


def test_h3_keys_are_compatible(tmp_path: Path) -> None:
    path = write_lora(tmp_path / NSGIRL, H3_HEADER)
    compat = check_lora_compatibility(path)
    assert compat.compatible is True
    assert compat.base_model == "minimax-h3-fl2va"
    assert compat.tensor_count == 3
    assert compat.key_prefixes == ["diffusion_model.blocks"]


def test_flux_keys_are_incompatible(tmp_path: Path) -> None:
    path = write_lora(tmp_path / FLUX_LORA, FLUX_HEADER)
    compat = check_lora_compatibility(path)
    assert compat.compatible is False
    assert compat.base_model == "flux"
    assert "transformer.single_transformer_blocks" in compat.reason
    assert "несовместима" in compat.reason


def test_sdxl_text_encoder_keys_are_incompatible(tmp_path: Path) -> None:
    path = write_lora(tmp_path / "NSFW_master.safetensors", SDXL_HEADER)
    compat = check_lora_compatibility(path)
    assert compat.compatible is False
    assert compat.base_model in {"sdxl", "sdxl-or-flux"}
    assert compat.reason


def test_unknown_architecture_has_reason(tmp_path: Path) -> None:
    path = write_lora(
        tmp_path / "mystery.safetensors",
        {"some.random.tensor": {"dtype": "F16", "shape": [1], "data_offsets": [0, 0]}},
    )
    compat = check_lora_compatibility(path)
    assert compat.compatible is False
    assert C.H3_COMPATIBLE_LORA_KEY_PREFIX in compat.reason


def test_unreadable_file_is_incompatible_not_raising(tmp_path: Path) -> None:
    path = tmp_path / "broken.safetensors"
    path.write_bytes(b"\x00" * 16)
    compat = check_lora_compatibility(path)
    assert compat.compatible is False
    assert "заголов" in compat.reason


def test_compat_to_dict_shape(tmp_path: Path) -> None:
    path = write_lora(tmp_path / NSGIRL, H3_HEADER)
    payload = check_lora_compatibility(path).to_dict()
    assert set(payload) >= {
        "compatible", "base_model", "key_prefixes", "tensor_count", "reason",
    }


def test_list_loras_in_dir_sorted(lora_dir: Path) -> None:
    write_lora(lora_dir / "aaa_first.safetensors", H3_HEADER)
    results = list_loras_in_dir(lora_dir)
    names = [item.name for item in results]
    assert names == sorted(names, key=str.lower)
    assert {item.name for item in results} == {"aaa_first.safetensors", NSGIRL, FLUX_LORA}
    assert {item.name: item.compatible for item in results}[FLUX_LORA] is False


def test_list_loras_in_dir_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        list_loras_in_dir(tmp_path / "nowhere")


# --------------------------------------------------------------------------- #
# Префлайт: фикстуры и коды выхода
# --------------------------------------------------------------------------- #


def test_preflight_valid_workflow_exits_zero(
    tmp_path: Path, lora_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_workflow(tmp_path / "valid.json", valid_workflow())
    code = h3_preflight.main(
        ["--workflow", str(path), "--lora-dir", str(lora_dir), "--source-size", "896x1280"]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "PREFLIGHT: OK" in out
    assert "ERRORS (0)" in out


def test_preflight_invalid_length_exits_one(
    tmp_path: Path, lora_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = valid_workflow()
    workflow["3"]["inputs"]["length"] = 200
    path = write_workflow(tmp_path / "bad_length.json", workflow)

    code = h3_preflight.main(
        ["--workflow", str(path), "--lora-dir", str(lora_dir), "--source-size", "896x1280"]
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "PREFLIGHT: FAILED" in out
    for length in C.VALID_LENGTHS:
        assert str(length) in out
    assert "Ближайшая валидная: 175" in out


def test_preflight_resolution_outside_grid_exits_one(
    tmp_path: Path, lora_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = valid_workflow()
    workflow["3"]["inputs"]["width"] = 900
    workflow["3"]["inputs"]["height"] = 1300
    path = write_workflow(tmp_path / "bad_res.json", workflow)

    code = h3_preflight.main(["--workflow", str(path), "--lora-dir", str(lora_dir)])
    out = capsys.readouterr().out
    assert code == 1
    assert "900x1300" in out


def test_preflight_ar_mismatch_exits_one(
    tmp_path: Path, lora_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Разрешение из сетки, но AR исходника другой — кадр поедет кропом."""
    workflow = valid_workflow()
    workflow["3"]["inputs"]["width"] = 1024
    workflow["3"]["inputs"]["height"] = 1216
    path = write_workflow(tmp_path / "ar_drift.json", workflow)

    code = h3_preflight.main(
        ["--workflow", str(path), "--lora-dir", str(lora_dir), "--source-size", "896x1280"]
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "AR" in out
    assert "896x1280" in out


def test_preflight_incompatible_lora_exits_one(
    tmp_path: Path, lora_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = valid_workflow()
    workflow["2"]["inputs"]["lora_name"] = FLUX_LORA
    path = write_workflow(tmp_path / "bad_lora.json", workflow)

    code = h3_preflight.main(
        ["--workflow", str(path), "--lora-dir", str(lora_dir), "--source-size", "896x1280"]
    )
    out = capsys.readouterr().out
    assert code == 1
    assert FLUX_LORA in out
    assert "несовместима" in out


def test_preflight_missing_lora_file_exits_one(
    tmp_path: Path, lora_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = valid_workflow()
    workflow["2"]["inputs"]["lora_name"] = "does_not_exist.safetensors"
    path = write_workflow(tmp_path / "missing_lora.json", workflow)

    code = h3_preflight.main(["--workflow", str(path), "--lora-dir", str(lora_dir)])
    out = capsys.readouterr().out
    assert code == 1
    assert "does_not_exist.safetensors" in out


def test_preflight_camera_zoom_conflict_is_warning(
    tmp_path: Path, lora_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = valid_workflow()
    workflow["3"]["inputs"]["prompt"] = "nsgirl, slow push in, camera moves closer"
    workflow["3"]["inputs"]["negative_prompt"] = "blurry, camera pan, camera zoom"
    path = write_workflow(tmp_path / "camera.json", workflow)

    code = h3_preflight.main(
        ["--workflow", str(path), "--lora-dir", str(lora_dir), "--source-size", "896x1280"]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "PREFLIGHT: OK" in out
    assert "WARNINGS (1)" in out
    assert C.RECOMMENDED_CAMERA_NEGATIVE in out


def test_preflight_shift_out_of_hard_range_is_error(lora_dir: Path) -> None:
    workflow = valid_workflow()
    workflow["3"]["inputs"]["shift_video"] = 40.0
    result = h3_preflight.run_preflight(workflow, lora_dirs=[lora_dir], source_ar=SOURCE_AR_896_1280)
    assert not result.ok
    assert any("shift_video" in message for message in result.errors)


def test_preflight_shift_outside_verified_band_is_warning(lora_dir: Path) -> None:
    workflow = valid_workflow()
    workflow["3"]["inputs"]["shift_video"] = 6.0
    result = h3_preflight.run_preflight(workflow, lora_dirs=[lora_dir], source_ar=SOURCE_AR_896_1280)
    assert result.ok
    assert any("shift_video" in message for message in result.warnings)


def test_preflight_missing_trigger_is_warning(lora_dir: Path) -> None:
    workflow = valid_workflow()
    workflow["3"]["inputs"]["prompt"] = "photorealistic skin, static hero framing"
    result = h3_preflight.run_preflight(workflow, lora_dirs=[lora_dir], source_ar=SOURCE_AR_896_1280)
    assert result.ok
    assert any("nsgirl" in message for message in result.warnings)


def test_preflight_conflicting_lora_pair_is_warning(lora_dir: Path) -> None:
    realism = "h3-realism-people-t2v-i2v-r2v.safetensors"
    write_lora(lora_dir / realism, H3_HEADER)
    workflow = valid_workflow()
    workflow["4"] = {
        "class_type": "LoraLoader",
        "inputs": {"lora_name": realism, "strength_model": 0.6, "strength_clip": 1.0},
    }
    result = h3_preflight.run_preflight(workflow, lora_dirs=[lora_dir], source_ar=SOURCE_AR_896_1280)
    assert result.ok
    assert any(realism in message for message in result.warnings)


def test_preflight_without_h3_node_is_error() -> None:
    result = h3_preflight.run_preflight({"1": {"class_type": "UNETLoader", "inputs": {}}})
    assert not result.ok
    assert any("MiniMax H3" in message for message in result.errors)


def test_preflight_lora_dir_unavailable_is_warning() -> None:
    result = h3_preflight.run_preflight(
        valid_workflow(), lora_dirs=None, source_ar=SOURCE_AR_896_1280
    )
    assert result.ok
    assert any("--lora-dir" in message for message in result.warnings)


# --------------------------------------------------------------------------- #
# Префлайт: ввод и устойчивость CLI
# --------------------------------------------------------------------------- #


def test_preflight_from_script(tmp_path: Path, lora_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    script = tmp_path / "render_v7.py"
    script.write_text(
        "import json\n"
        f"WORKFLOW = {valid_workflow()!r}\n"
        "if __name__ == '__main__':\n"
        "    print(json.dumps(WORKFLOW))\n",
        encoding="utf-8",
    )
    code = h3_preflight.main(
        ["--from-script", str(script), "--lora-dir", str(lora_dir), "--source-size", "896x1280"]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "PREFLIGHT: OK" in out


def test_preflight_from_script_without_workflow(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    script = tmp_path / "plain.py"
    script.write_text("print('hello')\n", encoding="utf-8")
    code = h3_preflight.main(["--from-script", str(script)])
    assert code == 2
    assert "error:" in capsys.readouterr().err


def test_preflight_broken_json_returns_two(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    code = h3_preflight.main(["--workflow", str(path)])
    assert code == 2
    assert "error:" in capsys.readouterr().err


def test_preflight_ui_format_rejected(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "ui.json"
    path.write_text(json.dumps({"nodes": [], "links": []}), encoding="utf-8")
    code = h3_preflight.main(["--workflow", str(path)])
    assert code == 2
    assert "API-формат" in capsys.readouterr().err


def test_preflight_requires_input(capsys: pytest.CaptureFixture[str]) -> None:
    assert h3_preflight.main([]) == 2
    assert "error:" in capsys.readouterr().err


def test_preflight_json_output(tmp_path: Path, lora_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = write_workflow(tmp_path / "valid.json", valid_workflow())
    code = h3_preflight.main(
        ["--workflow", str(path), "--lora-dir", str(lora_dir), "--source-ar", "0.7", "--json"]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["ok"] is True
    assert payload["errors"] == []


def test_read_image_size_png(tmp_path: Path) -> None:
    """Минимальный PNG: подпись + IHDR с размерами 896x1280."""
    import struct
    import zlib

    ihdr = struct.pack(">IIBBBBB", 896, 1280, 8, 6, 0, 0, 0)
    chunk = struct.pack(">I", len(ihdr)) + b"IHDR" + ihdr
    chunk += struct.pack(">I", zlib.crc32(b"IHDR" + ihdr))
    path = tmp_path / "source.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk)

    assert h3_preflight.read_image_size(path) == (896, 1280)


def test_read_image_size_unsupported_format(tmp_path: Path) -> None:
    path = tmp_path / "source.bmp"
    path.write_bytes(b"BM" + b"\x00" * 64)
    with pytest.raises(h3_preflight.WorkflowLoadError):
        h3_preflight.read_image_size(path)


def test_parse_source_size() -> None:
    assert h3_preflight.parse_source_size("896x1280") == (896, 1280)
    with pytest.raises(h3_preflight.WorkflowLoadError):
        h3_preflight.parse_source_size("896")


# --------------------------------------------------------------------------- #
# Selftest
# --------------------------------------------------------------------------- #


def test_selftest_reports_ok_and_failed(capsys: pytest.CaptureFixture[str]) -> None:
    code = h3_preflight.main(["--selftest"])
    out = capsys.readouterr().out
    assert code == 0
    assert "valid_workflow" in out and "OK" in out
    assert "invalid_workflow" in out and "FAILED" in out
    assert "3/3" in out


def test_selftest_fixtures_behave_as_documented() -> None:
    assert C.is_valid_length(h3_preflight.fixture_valid_workflow()["3"]["inputs"]["length"])
    assert not C.is_valid_length(h3_preflight.fixture_invalid_workflow()["3"]["inputs"]["length"])


# --------------------------------------------------------------------------- #
# pitfalls.md
# --------------------------------------------------------------------------- #


def test_pitfalls_catalogue_is_complete() -> None:
    pitfalls = (Path(__file__).resolve().parent.parent / "src" / "h3_knowledge" / "pitfalls.md")
    text = pitfalls.read_text(encoding="utf-8")

    for marker in (
        "20.6",   # разброс полос света на коже без last_frame
        "7.4",    # он же с двухточечным якорем
        "3.9",    # дверь без last_frame
        "1.2",    # дверь с last_frame
        "0.85-0.9",
        "0.9 → 0.65",
        "shift_video",
        "xfade",
        "camera pull back",
    ):
        assert marker in text, f"в pitfalls.md нет упоминания {marker!r}"

    table_rows = [line for line in text.splitlines() if line.startswith("| ") and "|" in line[2:]]
    # 10 граблей + строка заголовка + разделитель в сводной таблице.
    assert len([row for row in table_rows if row.startswith("| ")]) >= 12


def test_known_loras_use_verified_host_filenames() -> None:
    """Имена LoRA в каталоге совпадают с файлами на хосте."""
    assert "minimax-h3-facial-realism-closeup-cp2000.safetensors" in C.KNOWN_LORAS
    assert "h3-facial-realism-t2v-i2v-r2v.safetensors" not in C.KNOWN_LORAS
    facial = C.KNOWN_LORAS["minimax-h3-facial-realism-closeup-cp2000.safetensors"]
    assert facial["verified_filename"] is True
    assert facial["strength_clip"] == 0.35


def test_lora_dirs_contains_both_host_directories() -> None:
    """На хосте LoRA лежат в двух каталогах, а не в одном."""
    assert len(C.LORA_DIRS) >= 2
    assert any("comfyui" in d.lower() for d in C.LORA_DIRS)
    assert any("loras" in d.lower() for d in C.LORA_DIRS)


def test_rife_fps_constant() -> None:
    """H3 отдаёт 24 fps, после RIFE — 60."""
    assert C.FPS == 24
    assert C.RIFE_FPS == 60


def test_short_h3_key_layout_is_accepted() -> None:
    """Вторая H3-раскладка — ключи blocks.* без diffusion_model.

    Проверено на хосте: minimax-h3-facial-realism-closeup-cp2000.safetensors имеет
    ключи blocks.N.attn.* и base_model=MiniMax/MiniMax-H3 в метаданных.
    Без этого правила валидатор заваливал бы рабочую LoRA.
    """
    assert C.H3_SHORT_KEY_PREFIX == "blocks."
    header = {
        "blocks.25.attn.out_proj.lora_A.default.weight": {},
        "blocks.25.attn.out_proj.lora_B.default.weight": {},
    }
    compat = check_lora_compatibility_from_header(header)
    assert compat.compatible is True
    assert compat.base_model == H3_BASE_MODEL


def test_camera_marker_does_not_match_gaze_at_camera() -> None:
    """"gaze stays at the camera" — это взгляд, а не движение камеры."""
    assert "camera" not in C.CAMERA_PUSH_IN_MARKERS
    assert "zoom in" in C.CAMERA_PUSH_IN_MARKERS
    assert "push in" in C.CAMERA_PUSH_IN_MARKERS
