"""Tests for ``prompts.templates``."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from prompts.templates import TemplateLibrary


class TemplateLibraryTestCase(unittest.TestCase):
    def test_default_library_loads_five_templates(self) -> None:
        lib = TemplateLibrary()
        ids = set(lib.ids())
        self.assertEqual(
            ids,
            {
                "code_review",
                "debug_session",
                "documentation",
                "refactoring",
                "test_generation",
            },
        )

    def test_render_replaces_placeholders(self) -> None:
        lib = TemplateLibrary()
        text = lib.render(
            "code_review",
            {
                "language": "Go",
                "context": "Auth middleware",
                "code": "package auth\n",
                "focus_areas": "timing attacks",
            },
        )
        self.assertIn("Go", text)
        self.assertIn("package auth", text)
        self.assertNotIn("{{language}}", text)

    def test_optional_placeholder_defaults_to_empty(self) -> None:
        lib = TemplateLibrary()
        text = lib.render(
            "code_review",
            {
                "language": "Go",
                "context": "Auth middleware",
                "code": "x",
            },
        )
        self.assertNotIn("{{focus_areas}}", text)
        self.assertIn("**Focus (if relevant):**", text)

    def test_missing_required_raises(self) -> None:
        lib = TemplateLibrary()
        with self.assertRaises(KeyError):
            lib.render("code_review", {"language": "X"})

    def test_reload_picks_up_new_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "custom.json").write_text(
                json.dumps(
                    {
                        "id": "custom_smoke",
                        "name": "Smoke",
                        "description": "",
                        "body": "Hello {{name}}",
                        "placeholders": [
                            {"name": "name", "description": "who", "required": True},
                        ],
                        "expected_output_format": {"description": "plain"},
                        "examples": [],
                    }
                ),
                encoding="utf-8",
            )
            lib = TemplateLibrary(tmp_path)
            self.assertEqual(lib.render("custom_smoke", {"name": "world"}), "Hello world")
            lib.reload()
            self.assertIn("custom_smoke", lib.ids())


if __name__ == "__main__":
    unittest.main()
