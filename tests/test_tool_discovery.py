from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
DISCOVERY_SCRIPT = REPO_ROOT / "src" / "skills" / "tool_discovery.py"


class ToolDiscoveryTests(unittest.TestCase):
    def test_manifest_and_docs_are_current(self) -> None:
        """Fails when scripts/src changed without regenerating docs/tool_discovery/."""

        self.assertTrue(DISCOVERY_SCRIPT.is_file(), f"missing {DISCOVERY_SCRIPT}")
        proc = subprocess.run(
            [sys.executable, str(DISCOVERY_SCRIPT), "--check"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_manifest_lists_core_scripts(self) -> None:
        import json

        manifest_path = REPO_ROOT / "docs" / "tool_discovery" / "manifest.json"
        self.assertTrue(manifest_path.is_file(), "run tool_discovery.py --write")
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        ids = {entry["id"] for entry in data.get("tools", [])}
        for rel in (
            "scripts/batch_image_optimizer.py",
            "scripts/face_swap_batch.py",
            "scripts/comfy_auto_quality.py",
        ):
            with self.subTest(rel=rel):
                self.assertIn(rel, ids)


if __name__ == "__main__":
    unittest.main()
