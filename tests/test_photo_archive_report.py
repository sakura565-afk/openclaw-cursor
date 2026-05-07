from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import photo_archive_report  # noqa: E402


class PhotoArchiveReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def make_image(self, relative_path: str, size: tuple[int, int] = (32, 32), color: tuple[int, int, int] = (40, 80, 120)) -> Path:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", size, color)
        image.save(path)
        return path

    def write_bytes(self, relative_path: str, payload: bytes) -> Path:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path

    def test_build_report_counts_extensions_and_sizes(self) -> None:
        self.make_image("archive/a.jpg")
        self.make_image("archive/b.png")
        self.write_bytes("archive/video.mp4", b"\x00" * 4096)

        report = photo_archive_report.build_report(self.root / "archive", check_file_integrity=False, verbose=False)

        self.assertEqual(report["total_files"], 3)
        self.assertEqual(report["by_extension"]["jpg"], 1)
        self.assertEqual(report["by_extension"]["png"], 1)
        self.assertEqual(report["by_extension"]["mp4"], 1)
        self.assertFalse(report["integrity"]["checked"])
        self.assertEqual(report["integrity"]["broken_files_count"], 0)

        size_stats = report["size_stats"]
        self.assertGreater(size_stats["total_bytes"], 0)
        self.assertGreaterEqual(size_stats["max_bytes"], size_stats["median_bytes"])

    def test_integrity_flags_small_and_broken_files(self) -> None:
        self.make_image("archive/ok.jpg", size=(1024, 1024))
        self.write_bytes("archive/small.jpg", b"123")
        self.write_bytes("archive/broken.png", b"not-an-image")

        report = photo_archive_report.build_report(self.root / "archive", check_file_integrity=True, verbose=False)

        self.assertTrue(report["integrity"]["checked"])
        self.assertEqual(report["integrity"]["broken_files_count"], 2)
        issues = {Path(item["path"]).name: item["issue"] for item in report["integrity"]["broken_files"]}
        self.assertIn("possible_incomplete_file_size_lt_1kb", issues["small.jpg"])
        self.assertIn("broken_or_unsupported_image", issues["broken.png"])

    def test_cli_writes_markdown_and_json_reports(self) -> None:
        self.make_image("archive/photo.jpg")
        output_md = self.root / "out" / "photo_report.md"

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.photo_archive_report",
                "--scan",
                str(self.root / "archive"),
                "--output",
                str(output_md),
                "--check-integrity",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertTrue(output_md.exists())
        json_path = output_md.with_suffix(".json")
        self.assertTrue(json_path.exists())

        md_text = output_md.read_text(encoding="utf-8")
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertIn("# Photo Archive Report", md_text)
        self.assertEqual(payload["total_files"], 1)
        self.assertIn("by_extension", payload)
        self.assertIn("distribution_by_year_month", payload)


if __name__ == "__main__":
    unittest.main()
