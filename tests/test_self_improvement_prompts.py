"""Tests for ``src.self_improvement.prompts``."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from src.self_improvement import prompts


class SelfImprovementPromptsTestCase(unittest.TestCase):
    def test_list_includes_four_templates(self) -> None:
        ids = prompts.list_templates()
        self.assertEqual(
            list(ids),
            list(prompts.SELF_IMPROVEMENT_TEMPLATE_IDS),
        )

    def test_load_and_extract_placeholders(self) -> None:
        text = prompts.load_template("code_review")
        names = prompts.extract_placeholders(text)
        self.assertIn("agent_role", names)
        self.assertIn("diff_or_files", names)
        self.assertNotIn("language", names)

    def test_render_replaces_known_placeholders(self) -> None:
        rendered = prompts.render(
            "task_planning",
            {
                "agent_role": "Planner",
                "objective": "Ship templates",
                "known_constraints": "No new deps",
                "context": "OpenClaw repo",
                "existing_plan": "none",
                "verification_criteria": "unittest green",
                "risks_and_dependencies": "none",
                "output_format": "Markdown plan",
            },
        )
        self.assertIn("Ship templates", rendered)
        self.assertNotIn("{{objective}}", rendered)

    def test_render_strict_raises_on_missing(self) -> None:
        with self.assertRaises(KeyError):
            prompts.render("error_analysis", {"agent_role": "Reviewer"}, strict=True)

    def test_render_non_strict_fills_missing(self) -> None:
        rendered = prompts.render(
            "error_analysis",
            {"agent_role": "Reviewer"},
            strict=False,
            missing="TBD",
        )
        self.assertIn("Reviewer", rendered)
        self.assertIn("TBD", rendered)
        self.assertNotIn("{{error_description}}", rendered)

    def test_main_list_and_render(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = prompts.main(["list"])
        self.assertEqual(code, 0)
        self.assertIn("error_analysis", buf.getvalue())

        rendered_buf = io.StringIO()
        with redirect_stdout(rendered_buf):
            code = prompts.main(
                [
                    "render",
                    "session_review",
                    "--no-strict",
                    "--vars-json",
                    json.dumps({"agent_role": "Coach", "session_scope": "Today"}),
                ]
            )
        self.assertEqual(code, 0)
        self.assertIn("Coach", rendered_buf.getvalue())

    def test_custom_root_dir(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            template_dir = root / "prompts" / "templates"
            template_dir.mkdir(parents=True)
            (template_dir / "error_analysis.md").write_text(
                "Role: {{agent_role}}\nError: {{error_description}}\n",
                encoding="utf-8",
            )
            text = prompts.render(
                "error_analysis",
                {"agent_role": "A", "error_description": "B"},
                root=root,
            )
            self.assertEqual(text, "Role: A\nError: B\n")


if __name__ == "__main__":
    unittest.main()
