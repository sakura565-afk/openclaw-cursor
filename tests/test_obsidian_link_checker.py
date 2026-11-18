import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import obsidian_link_checker


class ObsidianLinkCheckerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp_dir.name) / "vault"
        self.vault.mkdir()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_valid_wiki_and_markdown_links(self):
        (self.vault / "b.md").write_text("# B\n## See Also\n", encoding="utf-8")
        (self.vault / "a.md").write_text(
            "Link [[b]] and [text](b.md#see-also)\n",
            encoding="utf-8",
        )
        rep = obsidian_link_checker.check_vault(self.vault, case_sensitive=True)
        self.assertEqual(rep["broken_count"], 0)

    def test_broken_wiki_file(self):
        (self.vault / "a.md").write_text("[[nope]]\n", encoding="utf-8")
        rep = obsidian_link_checker.check_vault(self.vault, case_sensitive=True)
        self.assertEqual(rep["broken_count"], 1)
        self.assertEqual(rep["broken_links"][0]["reason"], "file_not_found")

    def test_broken_anchor(self):
        (self.vault / "b.md").write_text("# B\n## Real Section\n", encoding="utf-8")
        (self.vault / "a.md").write_text("[[b#fake-heading]]\n", encoding="utf-8")
        rep = obsidian_link_checker.check_vault(self.vault, case_sensitive=True)
        self.assertEqual(rep["broken_count"], 1)
        self.assertEqual(rep["broken_links"][0]["reason"], "anchor_not_found")

    def test_same_file_heading_link(self):
        (self.vault / "a.md").write_text(
            "# Topic\n\nSee [[#topic]]\n",
            encoding="utf-8",
        )
        rep = obsidian_link_checker.check_vault(self.vault, case_sensitive=True)
        self.assertEqual(rep["broken_count"], 0)

    def test_ignores_links_in_fenced_code(self):
        (self.vault / "a.md").write_text(
            "```\n[[ghost]]\n```\nReal [[b]]\n",
            encoding="utf-8",
        )
        (self.vault / "b.md").write_text("# B\n", encoding="utf-8")
        rep = obsidian_link_checker.check_vault(self.vault, case_sensitive=True)
        self.assertEqual(rep["broken_count"], 0)

    def test_vault_relative_path(self):
        sub = self.vault / "sub"
        sub.mkdir()
        (sub / "n.md").write_text("# N\n", encoding="utf-8")
        (self.vault / "root.md").write_text("[[sub/n]]\n", encoding="utf-8")
        rep = obsidian_link_checker.check_vault(self.vault, case_sensitive=True)
        self.assertEqual(rep["broken_count"], 0)

    def test_relative_parent_path(self):
        sub = self.vault / "sub"
        sub.mkdir()
        (self.vault / "top.md").write_text("# T\n", encoding="utf-8")
        (sub / "inner.md").write_text("[[../top]]\n", encoding="utf-8")
        rep = obsidian_link_checker.check_vault(self.vault, case_sensitive=True)
        self.assertEqual(rep["broken_count"], 0)

    def test_case_insensitive_match(self):
        (self.vault / "Note.md").write_text("# N\n", encoding="utf-8")
        (self.vault / "a.md").write_text("[[note]]\n", encoding="utf-8")
        rep = obsidian_link_checker.check_vault(self.vault, case_sensitive=False)
        self.assertEqual(rep["broken_count"], 0)

    def test_case_sensitive_miss(self):
        (self.vault / "Note.md").write_text("# N\n", encoding="utf-8")
        (self.vault / "a.md").write_text("[[note]]\n", encoding="utf-8")
        rep = obsidian_link_checker.check_vault(self.vault, case_sensitive=True)
        self.assertEqual(rep["broken_count"], 1)

    def test_png_embed_resolves(self):
        (self.vault / "x.png").write_text("x", encoding="utf-8")
        (self.vault / "a.md").write_text("![](x.png)\n![[x.png]]\n", encoding="utf-8")
        rep = obsidian_link_checker.check_vault(self.vault, case_sensitive=True)
        self.assertEqual(rep["broken_count"], 0)

    def test_block_anchor(self):
        (self.vault / "b.md").write_text(
            "# B\n\nsome text\n\nparagraph ^blk1\n",
            encoding="utf-8",
        )
        (self.vault / "a.md").write_text("[[b#^blk1]]\n", encoding="utf-8")
        rep = obsidian_link_checker.check_vault(self.vault, case_sensitive=True)
        self.assertEqual(rep["broken_count"], 0)


if __name__ == "__main__":
    unittest.main()
