import csv
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

import piexif
from PIL import Image


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "exif_date_normalizer.py"
SPEC = importlib.util.spec_from_file_location("exif_date_normalizer", MODULE_PATH)
exif_date_normalizer = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = exif_date_normalizer
SPEC.loader.exec_module(exif_date_normalizer)


class ExifDateNormalizerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def create_jpg_with_exif(self, path: Path, exif_datetime: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (32, 32), color="red")
        exif_dict = {
            "Exif": {
                piexif.ExifIFD.DateTimeOriginal: exif_datetime.encode("utf-8"),
            }
        }
        image.save(path, exif=piexif.dump(exif_dict))

    def create_png(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGBA", (16, 16), color="blue")
        image.save(path)

    def test_preview_mode_writes_csv_and_keeps_name(self) -> None:
        source = self.root / "photo.jpg"
        self.create_jpg_with_exif(source, "2024:11:02 13:45:12")
        log_path = self.root / "preview.csv"

        exit_code = exif_date_normalizer.main(
            [
                "--scan",
                str(self.root),
                "--tz",
                "Europe/Moscow",
                "--csv-log",
                str(log_path),
            ]
        )

        self.assertEqual(0, exit_code)
        self.assertTrue(source.exists())

        with log_path.open("r", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(1, len(rows))
        self.assertEqual("photo.jpg", rows[0]["old_name"])
        self.assertEqual("preview", rows[0]["status"])
        self.assertEqual("exif", rows[0]["date_source"])
        self.assertEqual("2024-11-02_13-45-12_photo.jpg", rows[0]["new_name"])

    def test_fix_mode_renames_file_from_exif(self) -> None:
        source = self.root / "IMG_0001.JPG"
        self.create_jpg_with_exif(source, "2022:03:04 05:06:07")
        log_path = self.root / "apply.csv"

        exit_code = exif_date_normalizer.main(
            ["--scan", str(self.root), "--fix", "--csv-log", str(log_path)]
        )

        self.assertEqual(0, exit_code)
        renamed = self.root / "2022-03-04_05-06-07_IMG_0001.jpg"
        self.assertTrue(renamed.exists())
        self.assertFalse(source.exists())
        with log_path.open("r", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual("renamed", rows[0]["status"])

    def test_folder_date_fallback_for_png_without_exif(self) -> None:
        source = self.root / "trip_20240131" / "no_exif.png"
        self.create_png(source)
        log_path = self.root / "folder.csv"

        exit_code = exif_date_normalizer.main(
            [
                "--scan",
                str(self.root),
                "--fix",
                "--folder-date",
                "--csv-log",
                str(log_path),
            ]
        )

        self.assertEqual(0, exit_code)
        renamed = source.parent / "2024-01-31_00-00-00_no_exif.png"
        self.assertTrue(renamed.exists())
        with log_path.open("r", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual("folder_date", rows[0]["date_source"])
        self.assertEqual("renamed", rows[0]["status"])

    def test_file_without_date_is_skipped(self) -> None:
        source = self.root / "plain.png"
        self.create_png(source)
        log_path = self.root / "skip.csv"

        exif_date_normalizer.main(
            ["--scan", str(self.root), "--fix", "--csv-log", str(log_path)]
        )

        self.assertTrue(source.exists())
        with log_path.open("r", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual("skipped", rows[0]["status"])
        self.assertEqual("missing", rows[0]["date_source"])


if __name__ == "__main__":
    unittest.main()
