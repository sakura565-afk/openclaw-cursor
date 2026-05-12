from __future__ import annotations

import json
import shutil
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import tool_discovery  # noqa: E402


class ToolDiscoveryTests(unittest.TestCase):
    def _build_repo(self) -> Path:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        root = Path(tempdir.name)
        (root / "logs").mkdir(parents=True, exist_ok=True)
        (root / ".learnings").mkdir(parents=True, exist_ok=True)
        return root

    def test_normalize_tool_name_strips_noise(self) -> None:
        self.assertEqual(tool_discovery.normalize_tool_name("functions.read_file"), "read_file")
        self.assertEqual(tool_discovery.normalize_tool_name("  Bash(x) "), "Bash")

    def test_main_writes_tool_discovery_markdown(self) -> None:
        root = self._build_repo()
        session = {
            "messages": [
                {"role": "user", "content": "open a file"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"function": {"name": "read_file"}}],
                },
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"function": {"name": "read_file"}}],
                },
            ]
        }
        (root / "logs" / "sample.json").write_text(json.dumps(session), encoding="utf-8")
        (root / ".learnings" / "notes.md").write_text(
            "We should try invoking tool `grep` next.\n", encoding="utf-8"
        )

        out = root / ".learnings" / "tool_discovery.md"
        code = tool_discovery.main(["--root", str(root), "--output", str(out)])
        self.assertEqual(code, 0)
        text = out.read_text(encoding="utf-8")
        self.assertIn("read_file", text)
        self.assertIn("grep", text)
        self.assertIn("Tool-chain opportunities", text)

    def test_unused_skill_surfaces_when_catalog_present(self) -> None:
        root = self._build_repo()
        shutil.copy2(ROOT / "tool_discovery.py", root / "tool_discovery.py")
        skill_dir = root / "skills" / "phantom_skill"
        skill_dir.mkdir(parents=True)
        skill_dir.joinpath("SKILL.md").write_text(
            textwrap.dedent(
                """
                ---
                name: phantom-skill-xyz
                id: phantom-skill-xyz
                description: Never referenced in transcripts.
                ---
                Body.
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        (root / "logs" / "tiny.json").write_text(
            json.dumps({"messages": [{"role": "user", "content": "hello"}]}),
            encoding="utf-8",
        )
        out = root / ".learnings" / "out.md"
        tool_discovery.main(["--root", str(root), "--output", str(out)])
        body = out.read_text(encoding="utf-8")
        self.assertIn("phantom-skill-xyz", body)
        self.assertIn("Possibly unused", body)

    def test_bigram_read_file_pair_triggers_chain_section(self) -> None:
        root = self._build_repo()
        session = {
            "messages": [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"function": {"name": "read_file"}}],
                },
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"function": {"name": "read_file"}}],
                },
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"function": {"name": "read_file"}}],
                },
            ]
        }
        (root / "logs" / "chain.json").write_text(json.dumps(session), encoding="utf-8")
        out = root / ".learnings" / "out.md"
        tool_discovery.main(["--root", str(root), "--output", str(out)])
        body = out.read_text(encoding="utf-8")
        self.assertIn("read_file", body)
        self.assertIn("→", body)


if __name__ == "__main__":
    unittest.main()
