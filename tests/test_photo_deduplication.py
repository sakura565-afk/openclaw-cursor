import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from scripts import photo_deduplication


class PhotoDeduplicationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _create_image(self, path: Path, color: tuple[int, int, int], size: tuple[int, int] = (96, 96)) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", size, color)
        image.save(path)

    def _create_contrast_pattern(self, path: Path, size: tuple[int, int] = (96, 96)) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", size, (0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, size[0] // 2, size[1]), fill=(255, 255, 255))
        image.save(path)

    def test_build_duplicate_groups_detects_identical_images(self) -> None:
        img_a = self.root / "a.jpg"
        img_b = self.root / "b.jpg"
        img_c = self.root / "c.jpg"
        self._create_image(img_a, (200, 10, 10))
        self._create_image(img_b, (200, 10, 10))
        self._create_contrast_pattern(img_c)

        records = [
            photo_deduplication.hash_image(path, "both")
            for path in (img_a, img_b, img_c)
        ]
        valid_records = [item for item in records if item is not None]

        groups = photo_deduplication.build_duplicate_groups(valid_records, hash_type="both", threshold=95.0)
        self.assertEqual(1, len(groups))
        self.assertEqual(2, len(groups[0]))
        paths = {str(item.path) for item in groups[0]}
        self.assertIn(str(img_a), paths)
        self.assertIn(str(img_b), paths)

    def test_main_dry_run_writes_reports_without_deleting(self) -> None:
        scan_dir = self.root / "scan"
        json_report = self.root / "report.json"
        csv_report = self.root / "report.csv"
        img_a = scan_dir / "original.png"
        img_b = scan_dir / "copy.png"
        self._create_image(img_a, (40, 40, 220))
        self._create_image(img_b, (40, 40, 220))

        exit_code = photo_deduplication.main(
            [
                "--scan",
                str(scan_dir),
                "--dry-run",
                "--hash-type",
                "both",
                "--json-out",
                str(json_report),
                "--csv-out",
                str(csv_report),
            ]
        )

        self.assertEqual(0, exit_code)
        self.assertTrue(img_a.exists())
        self.assertTrue(img_b.exists())
        self.assertTrue(json_report.exists())
        self.assertTrue(csv_report.exists())
        payload = json.loads(json_report.read_text(encoding="utf-8"))
        self.assertEqual(1, payload["duplicate_groups"])
        self.assertEqual(1, payload["duplicate_files"])
        self.assertEqual("dry-run", payload["actions"][0]["action"])

    def test_main_move_moves_duplicates_to_duplicates_folder(self) -> None:
        scan_dir = self.root / "scan"
        img_a = scan_dir / "nested" / "original.jpg"
        img_b = scan_dir / "nested" / "copy.jpg"
        self._create_image(img_a, (120, 50, 50))
        self._create_image(img_b, (120, 50, 50))

        exit_code = photo_deduplication.main(
            [
                "--scan",
                str(scan_dir),
                "--move",
                "--hash-type",
                "perceptual",
                "--json-out",
                str(self.root / "move_report.json"),
                "--csv-out",
                str(self.root / "move_report.csv"),
            ]
        )

        self.assertEqual(0, exit_code)
        duplicates_dir = scan_dir / "duplicates"
        moved_files = list(duplicates_dir.glob("*.jpg"))
        self.assertEqual(1, len(moved_files))
        self.assertTrue(img_a.exists() or img_b.exists())

    def test_iter_image_paths_includes_raw_extensions(self) -> None:
        scan_dir = self.root / "scan"
        raw_file = scan_dir / "shot.cr2"
        raw_file.parent.mkdir(parents=True, exist_ok=True)
        raw_file.write_bytes(b"raw")

        paths = photo_deduplication.iter_image_paths(scan_dir)
        self.assertIn(raw_file, paths)

    def test_hash_image_returns_none_for_raw_without_rawpy(self) -> None:
        raw_file = self.root / "sample.nef"
        raw_file.write_bytes(b"raw")

        with patch("scripts.photo_deduplication.rawpy", None):
            record = photo_deduplication.hash_image(raw_file, "both")
        self.assertIsNone(record)


if __name__ == "__main__":
    unittest.main()
