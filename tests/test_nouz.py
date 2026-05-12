from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import nouz_search  # noqa: E402
from scripts import nouz_yaml_tagger  # noqa: E402


class NouzYamlTaggerTests(unittest.TestCase):
    def test_merge_only_missing_fields(self) -> None:
        meta = {"title": "x", "level": "task"}
        rel = Path("infra/net.md")
        updated = nouz_yaml_tagger.merge_nouz_defaults(meta, rel)
        self.assertEqual(updated["level"], "task")
        self.assertEqual(updated["role"], nouz_yaml_tagger.DEFAULT_ROLE)
        self.assertEqual(updated["status"], nouz_yaml_tagger.DEFAULT_STATUS)
        self.assertEqual(updated["domain"], "infra")
        self.assertIn("core_id", updated)

    def test_core_id_added_when_absent(self) -> None:
        meta: dict = {"status": "ready"}
        updated = nouz_yaml_tagger.merge_nouz_defaults(meta, Path("note.md"))
        self.assertIsNone(updated["core_id"])

    def test_cli_updates_vault(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            note = vault / "ai_topic.md"
            note.write_text("# Hello\nBody.\n", encoding="utf-8")
            code = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "scripts.nouz_yaml_tagger",
                    "--vault",
                    str(vault),
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(code.returncode, 0, msg=code.stderr)
            loaded = note.read_text(encoding="utf-8")
            self.assertIn("level:", loaded)
            self.assertIn("domain:", loaded)
            self.assertIn("ai", loaded)


class NouzSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.vault = self.root / "vault"
        self.data = self.root / "data"
        self.vault.mkdir(parents=True)

        (self.vault / "photo" / "shots.md").parent.mkdir(parents=True)
        (self.vault / "photo" / "shots.md").write_text(
            "---\ntags: [travel]\ndomain: photo\n---\n# Shots\nSee [[business/deal]].\n",
            encoding="utf-8",
        )
        (self.vault / "business" / "deal.md").parent.mkdir(parents=True)
        (self.vault / "business" / "deal.md").write_text(
            "---\nlevel: quant\ncore_id: alpha\n---\n# Deal\nLinked from photo.\n",
            encoding="utf-8",
        )

    def _search(self) -> nouz_search.NouzSearch:
        return nouz_search.NouzSearch(vault_path=self.vault, data_dir=self.data)

    def test_sync_and_find(self) -> None:
        ns = self._search()
        stats = ns.sync_index(embed=False)
        self.assertEqual(stats["notes"], 2)
        rows = ns.find_notes(domain="photo")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Shots")

    def test_find_by_level(self) -> None:
        ns = self._search()
        ns.sync_index(embed=False)
        found = ns.find_notes(level="quant")
        self.assertEqual(len(found), 1)
        self.assertIn("business", found[0]["path"])

    def test_core_profile_and_near(self) -> None:
        ns = self._search()
        ns.sync_index(embed=False)
        profile = ns.get_core_profile("alpha")
        self.assertEqual(profile["note_count"], 1)
        self.assertEqual(profile["core_id"], "alpha")
        near = ns.notes_near_core("alpha", limit=5)
        self.assertEqual(len(near), 1)

    def test_context_bundle_resolves_links(self) -> None:
        ns = self._search()
        ns.sync_index(embed=False)
        uid_photo = nouz_search.note_uid("photo/shots.md")
        bundle = ns.get_context_bundle(uid_photo, depth=1)
        self.assertIsNotNone(bundle["note"])
        paths_children = {c["path"] for c in bundle["children"]}
        self.assertIn("business/deal.md", paths_children)

    def test_semantic_search_with_fake_embedder(self) -> None:
        ns = self._search()
        ns.sync_index(embed=False)

        def fake_build() -> tuple[str, Any]:
            dim = 4

            def embed_fn(text: str, purpose: str) -> Any:
                _ = (text, purpose)
                v = np.zeros(dim, dtype=np.float32)
                v[0] = 1.0
                return v

            return ("test", embed_fn)

        with mock.patch.object(nouz_search, "build_embedder", fake_build):
            uid_deal = nouz_search.note_uid("business/deal.md")
            nouz_search.save_embedding(ns.embeddings_dir, uid_deal, np.array([1, 0, 0, 0], dtype=np.float32))
            rows = ns.semantic_search("anything", top_k=3)
            self.assertTrue(any(r["uid"] == uid_deal for r in rows))

    def test_cli_sync_json(self) -> None:
        ns = self._search()
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.nouz_search",
                "--vault",
                str(self.vault),
                "--data-dir",
                str(self.data),
                "sync",
                "--no-embed",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["notes"], 2)


if __name__ == "__main__":
    unittest.main()
