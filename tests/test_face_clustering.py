from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import face_clustering  # noqa: E402


class FakeBackend:
    def __init__(self, vectors: dict[str, list[list[float]]]) -> None:
        self.vectors = vectors
        self.calls = 0

    def encode(self, image_path: Path) -> list[np.ndarray]:
        self.calls += 1
        key = image_path.name
        return [np.asarray(item, dtype=float) for item in self.vectors.get(key, [])]


class FaceClusteringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.scan_dir = self.root / "photos"
        self.scan_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def make_image(self, relative_path: str) -> Path:
        path = self.scan_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (10, 10), color=(0, 0, 0)).save(path)
        return path

    def test_cluster_count_auto_assign_and_json_export(self) -> None:
        files = [
            self.make_image("a1.jpg"),
            self.make_image("a2.jpg"),
            self.make_image("b1.jpg"),
            self.make_image("b2.jpg"),
        ]
        backend = FakeBackend(
            {
                "a1.jpg": [[0.0, 0.0]],
                "a2.jpg": [[0.02, 0.0]],
                "b1.jpg": [[1.0, 1.0]],
                "b2.jpg": [[1.03, 1.0]],
            }
        )
        catalog_path = self.root / "catalog.json"
        cache_path = self.root / "cache.json"

        with patch("scripts.face_clustering.pick_backend", return_value=backend):
            catalog = face_clustering.run(
                scan_path=self.scan_dir,
                cluster_count=2,
                min_samples=2,
                export_json=True,
                export_folders_flag=False,
                backend_name="auto",
                catalog_path=catalog_path,
                cache_path=cache_path,
                folders_dir=self.root / "folders",
            )

        self.assertEqual(len(catalog["clusters"]), 2)
        self.assertEqual(sum(cluster["size"] for cluster in catalog["clusters"]), 4)
        self.assertTrue(catalog_path.exists())
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
        listed_files = [item["path"] for cluster in payload["clusters"] for item in cluster["files"]]
        self.assertEqual(sorted(listed_files), sorted(str(file.relative_to(self.scan_dir)) for file in files))
        self.assertEqual(backend.calls, 4)

    def test_cached_encodings_skip_reprocessing(self) -> None:
        self.make_image("one.jpg")
        self.make_image("two.jpg")
        backend = FakeBackend({"one.jpg": [[0.0, 0.0]], "two.jpg": [[1.0, 1.0]]})
        cache_path = self.root / "cache.json"

        with patch("scripts.face_clustering.pick_backend", return_value=backend):
            face_clustering.run(
                scan_path=self.scan_dir,
                cluster_count=None,
                min_samples=1,
                export_json=False,
                export_folders_flag=False,
                backend_name="auto",
                catalog_path=self.root / "catalog.json",
                cache_path=cache_path,
                folders_dir=self.root / "folders",
            )
            first_calls = backend.calls
            face_clustering.run(
                scan_path=self.scan_dir,
                cluster_count=None,
                min_samples=1,
                export_json=False,
                export_folders_flag=False,
                backend_name="auto",
                catalog_path=self.root / "catalog.json",
                cache_path=cache_path,
                folders_dir=self.root / "folders",
            )

        self.assertEqual(first_calls, 2)
        self.assertEqual(backend.calls, 2)

    def test_export_folders_creates_symlinks(self) -> None:
        self.make_image("x1.jpg")
        self.make_image("x2.jpg")
        backend = FakeBackend({"x1.jpg": [[0.0, 0.0]], "x2.jpg": [[0.01, 0.01]]})
        export_dir = self.root / "clusters"

        with patch("scripts.face_clustering.pick_backend", return_value=backend):
            face_clustering.run(
                scan_path=self.scan_dir,
                cluster_count=1,
                min_samples=1,
                export_json=False,
                export_folders_flag=True,
                backend_name="auto",
                catalog_path=self.root / "catalog.json",
                cache_path=self.root / "cache.json",
                folders_dir=export_dir,
            )

        person_dir = export_dir / "person_001"
        self.assertTrue(person_dir.exists())
        links = sorted(person_dir.iterdir())
        self.assertEqual(len(links), 2)
        self.assertTrue(all(item.is_symlink() for item in links))


if __name__ == "__main__":
    unittest.main()
