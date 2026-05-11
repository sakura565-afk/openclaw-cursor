"""Tests for repository-root ``tool_discovery`` (SKILL.md catalog + intelligence)."""

from __future__ import annotations

import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tool_discovery as td  # noqa: E402


class ToolDiscoveryIntelligenceTests(unittest.TestCase):
    def _repo(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name)

    def test_api_feature_detection_and_companion_capabilities(self) -> None:
        root = self._repo()
        skill_dir = root / "pack" / "http_skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "client.py").write_text(
            textwrap.dedent(
                """
                import requests

                def fetch_status(url: str):
                    return requests.get(url, timeout=5)
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        (skill_dir / "SKILL.md").write_text(
            textwrap.dedent(
                """
                ---
                name: HTTP Helper
                description: Calls REST https://api.example.com/v1/status for health checks.
                capabilities:
                  - Network and remote API access
                ---
                # Skill

                Use GET /v1/status with a Bearer token.
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )

        disc = td.ToolDiscovery(roots=[root], usage_scan_roots=[root])
        tools = disc.discover_all()
        self.assertEqual(len(tools), 1)
        t = tools[0]
        self.assertTrue(any("requests" in x.lower() for x in t.api_features))
        self.assertTrue(any("http" in x.lower() for x in t.api_features))
        self.assertTrue(t.automatic_capabilities)

    def test_cross_module_usage_and_intelligence_json(self) -> None:
        root = self._repo()
        skill_dir = root / "svc" / "alpha"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            textwrap.dedent(
                """
                ---
                name: Alpha Skill
                id: alpha-skill
                description: First skill.
                actions:
                  - Do one thing
                limitations:
                  - Local only
                ---
                # Alpha
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        lib = root / "lib" / "runner"
        lib.mkdir(parents=True)
        (lib / "invoke.py").write_text(
            '"""Uses alpha."""\nSKILL_PATH = "svc/alpha/SKILL.md"\nALPHA = "alpha-skill"\n',
            encoding="utf-8",
        )

        disc = td.ToolDiscovery(roots=[root], usage_scan_roots=[root])
        catalog = disc.discover_all()
        intel = disc.compile_intelligence(catalog)
        refs = intel.usage_by_skill.get("alpha-skill", [])
        self.assertTrue(refs)
        self.assertEqual(refs[0].module_bucket, "lib")
        payload = intel.to_dict()
        self.assertIn("cross_module_usage", payload)
        self.assertIn("suggestions", payload)
        self.assertGreaterEqual(payload["usage_patterns"]["references_total"], 1)

    def test_gap_suggestions_for_missing_actions(self) -> None:
        root = self._repo()
        a = root / "a" / "SKILL.md"
        b = root / "b" / "SKILL.md"
        a.parent.mkdir(parents=True)
        b.parent.mkdir(parents=True)
        (a).write_text(
            "---\nname: One\ndescription: No actions here.\n---\n\nBody.\n",
            encoding="utf-8",
        )
        (b).write_text(
            "---\nname: Two\ndescription: Other.\nactions:\n  - Run\n---\n\nBody.\n",
            encoding="utf-8",
        )
        disc = td.ToolDiscovery(roots=[root], usage_scan_roots=[root])
        catalog = disc.discover_all()
        intel = disc.compile_intelligence(catalog)
        kinds = {g["kind"] for g in intel.gaps}
        self.assertIn("missing_actions", kinds)
        self.assertTrue(any("actions" in s.lower() for s in intel.suggestions))


if __name__ == "__main__":
    unittest.main()
