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

from scripts import tool_discovery  # noqa: E402


class ToolDiscoveryRegistryTests(unittest.TestCase):
    def _make_minimal_workspace(self) -> Path:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        ws = Path(td.name)
        (ws / "scripts").mkdir(parents=True)
        (ws / "scripts" / "demo_batch.py").write_text(
            '"""Batch automation for nightly queues."""\n',
            encoding="utf-8",
        )
        skill_dir = ws / "skills" / "myskill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            textwrap.dedent(
                """
                ---
                name: My Image Skill
                description: Resize PNG assets for the gallery.
                tags:
                  - image
                  - custom-tag
                ---

                Body text.
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        return ws

    def test_scan_entries_finds_script_and_skill(self) -> None:
        ws = self._make_minimal_workspace()
        roots = tool_discovery.collect_scan_roots(
            ws,
            ROOT,
            include_repo=False,
            include_global_skills=False,
        )
        entries = tool_discovery.scan_entries(roots)
        by_name = {e.name: e for e in entries}
        self.assertIn("demo_batch", by_name)
        self.assertEqual(by_name["demo_batch"].kind, "script")
        self.assertIn("automation", by_name["demo_batch"].tags)
        self.assertIn("My Image Skill", by_name)
        skill = by_name["My Image Skill"]
        self.assertEqual(skill.kind, "skill")
        self.assertIn("image", skill.tags)
        self.assertIn("custom-tag", skill.tags)

    def test_diff_detects_modified_mtime(self) -> None:
        ws = self._make_minimal_workspace()
        roots = tool_discovery.collect_scan_roots(
            ws,
            ROOT,
            include_repo=False,
            include_global_skills=False,
        )
        first = tool_discovery.build_registry_payload(roots, None)
        script_path = ws / "scripts" / "demo_batch.py"
        first_tool = next(t for t in first["tools"] if t["name"] == "demo_batch")
        self.assertIn("added", first["changes_since_previous_run"])
        self.assertGreaterEqual(len(first["changes_since_previous_run"]["added"]), 1)

        # Bump mtime without changing size much
        script_path.write_text(
            '"""Batch automation for nightly queues (v2)."""\n',
            encoding="utf-8",
        )
        second = tool_discovery.build_registry_payload(roots, first)
        modified = second["changes_since_previous_run"]["modified"]
        self.assertIn(first_tool["path"], modified)

    def test_filter_entries_by_tag(self) -> None:
        ws = self._make_minimal_workspace()
        roots = tool_discovery.collect_scan_roots(
            ws,
            ROOT,
            include_repo=False,
            include_global_skills=False,
        )
        payload = tool_discovery.build_registry_payload(roots, None)
        entries = tool_discovery.entries_from_payload(payload)
        hits = tool_discovery.filter_entries(entries, tag="image")
        self.assertTrue(any(e.name == "My Image Skill" for e in hits))
        self.assertFalse(any(e.name == "demo_batch" for e in hits))

    def test_main_list_and_search_json(self) -> None:
        ws = self._make_minimal_workspace()
        reg = ws / "tool_registry.json"
        buf = io.StringIO()
        prev = sys.stdout
        try:
            sys.stdout = buf
            code = tool_discovery.main(
                [
                    "--workspace",
                    str(ws),
                    "--registry",
                    str(reg),
                    "--no-repo",
                    "--json",
                    "list",
                ]
            )
        finally:
            sys.stdout = prev
        self.assertEqual(code, 0)
        data = json.loads(buf.getvalue())
        self.assertGreaterEqual(len(data), 2)
        names = {row["name"] for row in data}
        self.assertIn("demo_batch", names)

        buf2 = io.StringIO()
        try:
            sys.stdout = buf2
            code2 = tool_discovery.main(
                [
                    "--workspace",
                    str(ws),
                    "--registry",
                    str(reg),
                    "--no-repo",
                    "--json",
                    "search",
                    "--tag",
                    "image",
                ]
            )
        finally:
            sys.stdout = prev
        self.assertEqual(code2, 0)
        found = json.loads(buf2.getvalue())
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["name"], "My Image Skill")

    def test_main_show_found_and_not_found(self) -> None:
        ws = self._make_minimal_workspace()
        reg = ws / "tool_registry.json"
        buf = io.StringIO()
        prev = sys.stdout
        try:
            sys.stdout = buf
            code = tool_discovery.main(
                [
                    "--workspace",
                    str(ws),
                    "--registry",
                    str(reg),
                    "--no-repo",
                    "--json",
                    "show",
                    "demo_batch",
                ]
            )
        finally:
            sys.stdout = prev
        self.assertEqual(code, 0)
        row = json.loads(buf.getvalue())
        self.assertEqual(row["name"], "demo_batch")

        buf_miss = io.StringIO()
        try:
            sys.stdout = buf_miss
            code_missing = tool_discovery.main(
                [
                    "--workspace",
                    str(ws),
                    "--registry",
                    str(reg),
                    "--no-repo",
                    "--json",
                    "show",
                    "nonexistent_tool_xyz",
                ]
            )
        finally:
            sys.stdout = prev
        self.assertEqual(code_missing, 2)
        err_payload = json.loads(buf_miss.getvalue())
        self.assertEqual(err_payload.get("error"), "not_found")

    def test_default_command_writes_registry(self) -> None:
        ws = self._make_minimal_workspace()
        reg = ws / "tool_registry.json"
        err = io.StringIO()
        prev_err = sys.stderr
        try:
            sys.stderr = err
            code = tool_discovery.main(
                [
                    "--workspace",
                    str(ws),
                    "--registry",
                    str(reg),
                    "--no-repo",
                ]
            )
        finally:
            sys.stderr = prev_err
        self.assertEqual(code, 0)
        self.assertTrue(reg.is_file())
        self.assertIn("Registry updated", err.getvalue())


if __name__ == "__main__":
    unittest.main()
