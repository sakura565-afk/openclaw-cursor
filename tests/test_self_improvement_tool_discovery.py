from __future__ import annotations

import io
import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.self_improvement import tool_discovery as td  # noqa: E402


class SelfImprovementToolDiscoveryTests(unittest.TestCase):
    def _repo(self) -> Path:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        return Path(tempdir.name)

    def test_discover_skill_tools_parses_frontmatter_and_capabilities(self) -> None:
        root = self._repo()
        skill = root / "skills" / "alpha" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(
            textwrap.dedent(
                """
                ---
                title: Alpha Skill
                description: Does alpha things.
                ---

                ## Capabilities
                - custom cap one
                - custom cap two
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        entries = td.discover_skill_tools(root / "skills", root)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].name, "Alpha Skill")
        self.assertTrue(entries[0].id.startswith("skill:skills/"))
        self.assertEqual(entries[0].capabilities, ["custom cap one", "custom cap two"])

    def test_discover_from_tools_md_sections(self) -> None:
        root = self._repo()
        tm = root / "TOOLS.md"
        tm.write_text(
            textwrap.dedent(
                """
                # Tools

                ## Backup Runner

                Copies files safely.

                ### Capabilities
                - filesystem
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        entries = td.discover_from_tools_md(tm, root)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].name, "Backup Runner")
        self.assertEqual(entries[0].source, "tools_md")
        self.assertIn("filesystem", entries[0].capabilities)

    def test_track_usage_and_list_tools(self) -> None:
        root = self._repo()
        usage = root / "u.json"
        skill = root / "skills" / "x.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("# X\n\nBody.\n", encoding="utf-8")
        e = td.discover_skill_tools(root / "skills", root)[0]

        buf = io.StringIO()
        old = sys.stdout
        try:
            sys.stdout = buf
            td.main(["--root", str(root), "--usage-file", str(usage), "track-usage", "--tool", e.id, "--count", "2"])
        finally:
            sys.stdout = old
        self.assertTrue(usage.is_file())
        out = json.loads(buf.getvalue())
        self.assertTrue(out["ok"])

        buf2 = io.StringIO()
        try:
            sys.stdout = buf2
            td.main(["--root", str(root), "--usage-file", str(usage), "list-tools", "--no-include-scripts"])
        finally:
            sys.stdout = old
        payload = json.loads(buf2.getvalue())
        row = next(r for r in payload["tools"] if r["id"] == e.id)
        self.assertEqual(row["usage_count"], 2)
        self.assertIn("combo_usage", payload)

    def test_suggest_combos_scores_shared_capabilities(self) -> None:
        a = td.CatalogEntry(
            id="a",
            name="a",
            source="skill",
            rel_path=None,
            description="queue monitor",
            capabilities=["Queue orchestration", "Monitoring and observability"],
        )
        b = td.CatalogEntry(
            id="b",
            name="b",
            source="script",
            rel_path="scripts/q.py",
            description="analytics",
            capabilities=["Queue orchestration", "Analytics and reporting"],
            io_profile=["filesystem"],
        )
        combos = td.suggest_combinations([a, b], top_n=3)
        self.assertGreaterEqual(len(combos), 1)
        self.assertIn("Shared capabilities", " ".join(combos[0]["reasoning"]))

    def test_update_docs_inserts_markers(self) -> None:
        root = self._repo()
        (root / "TOOLS.md").write_text("# Tools\n\nIntro.\n", encoding="utf-8")
        catalog = [
            td.CatalogEntry(
                id="tools_md:demo",
                name="Demo",
                source="tools_md",
                rel_path="TOOLS.md",
                description="Demo tool for tests.",
                capabilities=["General utility automation"],
            )
        ]
        body = td.update_tools_md(root, catalog, td.empty_usage_store(), dry_run=False)
        self.assertIn(td.MARKER_BEGIN, body)
        self.assertIn("Catalog summary", body)
        self.assertIn("demo", (root / "TOOLS.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
