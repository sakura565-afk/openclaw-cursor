from pathlib import Path

from PIL import Image

from media_tool import process_directory


def test_process_directory_creates_faded_images_and_summary(tmp_path: Path) -> None:
    input_dir = Path("tests/fixtures")
    output_dir = tmp_path / "processed"
    summary_path = tmp_path / "image-summary.md"

    results = process_directory(
        input_dir=input_dir,
        output_dir=output_dir,
        summary_path=summary_path,
        fade_strength=0.6,
    )

    assert len(results) == 2

    output_paths = sorted(output_dir.glob("*.png"))
    assert [path.name for path in output_paths] == ["forest-faded.png", "sunset-faded.png"]

    for output_path in output_paths:
        with Image.open(output_path) as processed:
            assert processed.mode == "RGBA"
            assert processed.size == (4, 4)
            top_alpha = processed.getpixel((0, 0))[3]
            bottom_alpha = processed.getpixel((0, processed.height - 1))[3]
            assert bottom_alpha < top_alpha

    summary = summary_path.read_text(encoding="utf-8")
    assert "# Image Processing Summary" in summary
    assert "| forest.ppm |" in summary
    assert "| sunset.ppm |" in summary
    assert "| Total |" in summary
