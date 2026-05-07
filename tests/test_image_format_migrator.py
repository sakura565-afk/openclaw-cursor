from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from scripts import image_format_migrator as migrator


class ImageFormatMigratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _create_image(self, path: Path, fmt: str, color: tuple[int, int, int] = (120, 80, 60)) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (64, 64), color).save(path, format=fmt)
        return path

    def test_single_png_converts_next_to_source_by_default(self) -> None:
        source = self._create_image(self.root / "photo.png", "PNG")

        exit_code = migrator.main(["--single", str(source)])

        self.assertEqual(0, exit_code)
        self.assertTrue((self.root / "photo_converted.jpg").exists())

    def test_scan_uses_default_output_suffix_directory(self) -> None:
        scan_dir = self.root / "archive"
        self._create_image(scan_dir / "a.png", "PNG")
        self._create_image(scan_dir / "nested" / "b.bmp", "BMP")

        exit_code = migrator.main(["--scan", str(scan_dir)])

        out_dir = self.root / "archive_converted"
        self.assertEqual(0, exit_code)
        self.assertTrue((out_dir / "a.jpg").exists())
        self.assertTrue((out_dir / "b.jpg").exists())

    def test_dry_run_creates_no_files(self) -> None:
        source = self._create_image(self.root / "dry.png", "PNG")

        exit_code = migrator.main(["--single", str(source), "--dry-run"])

        self.assertEqual(0, exit_code)
        self.assertFalse((self.root / "dry_converted.jpg").exists())

    def test_overwrite_replaces_source_with_jpeg(self) -> None:
        source = self._create_image(self.root / "orig.bmp", "BMP")

        exit_code = migrator.main(["--single", str(source), "--overwrite"])

        self.assertEqual(0, exit_code)
        self.assertFalse(source.exists())
        self.assertTrue((self.root / "orig.jpg").exists())

    def test_quality_is_applied_for_jpeg_compression(self) -> None:
        source = self._create_image(self.root / "source.jpg", "JPEG")
        low = self.root / "low"
        high = self.root / "high"

        low_code = migrator.main(
            ["--single", str(source), "--quality", "40", "--output", str(low), "--no-preserve-exif"]
        )
        high_code = migrator.main(
            ["--single", str(source), "--quality", "95", "--output", str(high), "--no-preserve-exif"]
        )

        low_size = (low / "source.jpg").stat().st_size
        high_size = (high / "source.jpg").stat().st_size
        self.assertEqual(0, low_code)
        self.assertEqual(0, high_code)
        self.assertLess(low_size, high_size)

    def test_mutually_exclusive_mode_flags(self) -> None:
        with self.assertRaises(SystemExit):
            migrator.parse_args(["--single", "a.jpg", "--scan", "folder"])


if __name__ == "__main__":
    unittest.main()
