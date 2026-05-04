import importlib.util
import io
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "media_tool.py"


def save_image(path: Path, *, size=(800, 600), color=(120, 30, 200), fmt="PNG") -> None:
    image = Image.new("RGB", size, color)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format=fmt)


def run_cli(*args: str, input_bytes: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def load_media_tool_module():
    spec = importlib.util.spec_from_file_location("media_tool_module", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    return module


def open_image(path: Path) -> Image.Image:
    image = Image.open(path)
    image.load()
    return image


def test_resize_limits_longest_side_and_file_size(tmp_path: Path) -> None:
    source = tmp_path / "large.png"
    output = tmp_path / "resized.jpg"
    save_image(source, size=(5000, 2500), fmt="PNG")

    result = run_cli("resize", str(source), str(output), "--quality", "85")

    assert result.returncode == 0, result.stderr.decode()
    assert output.exists()
    assert output.stat().st_size <= 10 * 1024 * 1024
    with open_image(output) as image:
        assert max(image.size) <= 3840


def test_thumbnail_preserves_aspect_ratio_and_max_side(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    thumb = tmp_path / "thumb.png"
    save_image(source, size=(1200, 400), fmt="PNG")

    result = run_cli("thumb", str(source), str(thumb))

    assert result.returncode == 0, result.stderr.decode()
    with open_image(thumb) as image:
        assert image.size == (300, 100)


def test_convert_supports_stdin_stdout_pipeline() -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 32), (10, 20, 30)).save(buffer, format="PNG")

    result = run_cli("convert", "-", "-", "--format", "webp", input_bytes=buffer.getvalue())

    assert result.returncode == 0, result.stderr.decode()
    converted = Image.open(io.BytesIO(result.stdout))
    converted.load()
    assert converted.format == "WEBP"
    assert converted.size == (64, 32)


def test_compress_reduces_jpeg_size_with_lower_quality(tmp_path: Path) -> None:
    source = tmp_path / "source.jpg"
    high = tmp_path / "high.jpg"
    low = tmp_path / "low.jpg"
    save_image(source, size=(1600, 1200), fmt="JPEG")

    high_result = run_cli("compress", str(source), str(high), "--quality", "95")
    low_result = run_cli("compress", str(source), str(low), "--quality", "40")

    assert high_result.returncode == 0, high_result.stderr.decode()
    assert low_result.returncode == 0, low_result.stderr.decode()
    assert low.stat().st_size <= high.stat().st_size


def test_batch_reads_stdin_paths_and_reports_progress(tmp_path: Path) -> None:
    source_a = tmp_path / "a.png"
    source_b = tmp_path / "b.png"
    output_dir = tmp_path / "out"
    save_image(source_a, size=(1000, 500), fmt="PNG")
    save_image(source_b, size=(600, 300), fmt="PNG")
    stdin_payload = f"{source_a}\n{source_b}\n".encode()

    result = run_cli(
        "batch",
        str(output_dir),
        "--operation",
        "thumb",
        "--workers",
        "2",
        input_bytes=stdin_payload,
    )

    assert result.returncode == 0, result.stderr.decode()
    stdout_lines = result.stdout.decode().strip().splitlines()
    stderr_text = result.stderr.decode()

    assert len(stdout_lines) == 2
    assert "[1/2] thumb" in stderr_text
    assert "[2/2] thumb" in stderr_text

    outputs = sorted(output_dir.glob("*.png"))
    assert len(outputs) == 2
    for output in outputs:
        with open_image(output) as image:
            assert max(image.size) <= 300


def test_batch_parallel_convert_writes_requested_format(tmp_path: Path) -> None:
    source_a = tmp_path / "a.png"
    source_b = tmp_path / "b.png"
    output_dir = tmp_path / "out"
    save_image(source_a, size=(640, 480), fmt="PNG")
    save_image(source_b, size=(320, 200), fmt="PNG")

    result = run_cli(
        "batch",
        str(output_dir),
        str(source_a),
        str(source_b),
        "--operation",
        "convert",
        "--format",
        "webp",
        "--workers",
        "3",
    )

    assert result.returncode == 0, result.stderr.decode()
    outputs = sorted(output_dir.glob("*.webp"))
    assert [path.name for path in outputs] == ["a.webp", "b.webp"]
    for output in outputs:
        with open_image(output) as image:
            assert image.format == "WEBP"


def test_batch_uses_multiple_workers_when_requested(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    media_tool = load_media_tool_module()
    output_dir = tmp_path / "out"
    state = {"active": 0, "max_active": 0}
    ready = threading.Event()
    lock = threading.Lock()

    def fake_process_batch_job(job):
        with lock:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
            if state["active"] >= 2:
                ready.set()
        ready.wait(timeout=2)
        time.sleep(0.05)
        with lock:
            state["active"] -= 1

        destination = output_dir / f"{Path(job.input_path).stem}.png"
        return media_tool.BatchResult(
            index=job.index,
            total=job.total,
            input_path=job.input_path,
            destination=destination,
            rendered=f"processed-{job.index}".encode(),
            operation=job.operation,
        )

    monkeypatch.setattr(media_tool, "process_batch_job", fake_process_batch_job)

    args = media_tool.build_parser().parse_args(
        [
            "batch",
            str(output_dir),
            "one.png",
            "two.png",
            "three.png",
            "four.png",
            "--operation",
            "thumb",
            "--workers",
            "4",
        ]
    )

    assert args.handler(args) == 0
    assert state["max_active"] >= 2
    assert sorted(path.name for path in output_dir.glob("*.png")) == [
        "four.png",
        "one.png",
        "three.png",
        "two.png",
    ]


def test_batch_rejects_workers_above_max(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    save_image(source)

    result = run_cli("batch", str(tmp_path / "out"), str(source), "--workers", "9")

    assert result.returncode != 0
    assert b"workers" in result.stderr.lower()


def test_invalid_quality_returns_error(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    save_image(source)

    result = run_cli("compress", str(source), "-", "--quality", "101")

    assert result.returncode != 0
    assert b"quality" in result.stderr.lower()
