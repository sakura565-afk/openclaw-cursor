from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import ollama_manifest_fix as omf  # noqa: E402


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class OllamaManifestFixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        os.environ["OLLAMA_MODELS"] = str(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()
        os.environ.pop("OLLAMA_MODELS", None)

    def test_canonicalize_digest_variants(self) -> None:
        h = "a" * 64
        self.assertEqual(omf.canonicalize_digest(f"SHA256:{h}"), f"sha256:{h}")
        self.assertEqual(omf.canonicalize_digest(f"sha256-{h}"), f"sha256:{h}")
        self.assertEqual(omf.canonicalize_digest(h), f"sha256:{h}")
        self.assertIsNone(omf.canonicalize_digest("bogus"))

    def test_fix_manifest_updates_sizes_and_mirrors_host(self) -> None:
        cfg_blob = b'{"dummy":true}'
        layer_blob = b"gguf-binary-placeholder"
        d_cfg = _sha256(cfg_blob)
        d_layer = _sha256(layer_blob)
        blobs = self.root / "blobs"
        blobs.mkdir(parents=True)
        (blobs / d_cfg.replace(":", "-", 1)).write_bytes(cfg_blob)
        (blobs / d_layer.replace(":", "-", 1)).write_bytes(layer_blob)

        wrong_manifest = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
            "config": {
                "mediaType": "application/vnd.ollama.image.model",
                "digest": d_cfg.replace("sha256:", "sha256-"),
                "size": 999,
            },
            "layers": [{"mediaType": "application/vnd.ollama.image.model", "digest": d_layer, "size": 1}],
        }

        legacy = (
            self.root
            / "manifests"
            / "registry.ollama.com"
            / "library"
            / "nomic-embed-text"
            / "latest"
        )
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(json.dumps(wrong_manifest), encoding="utf-8")

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = omf.run_fix(
                self.root,
                dry_run=False,
                repair=True,
                mirror_aliases=True,
                paths=None,
            )
        self.assertEqual(code, 0)

        canonical = (
            self.root
            / "manifests"
            / omf.DEFAULT_REGISTRY
            / "library"
            / "nomic-embed-text"
            / "latest"
        )
        self.assertTrue(canonical.is_file())
        fixed = json.loads(canonical.read_text(encoding="utf-8"))
        self.assertEqual(fixed["config"]["digest"], d_cfg)
        self.assertEqual(fixed["config"]["size"], len(cfg_blob))
        self.assertEqual(fixed["layers"][0]["digest"], d_layer)
        self.assertEqual(fixed["layers"][0]["size"], len(layer_blob))

        fixed_legacy = json.loads(legacy.read_text(encoding="utf-8"))
        self.assertEqual(fixed_legacy["config"]["size"], len(cfg_blob))
