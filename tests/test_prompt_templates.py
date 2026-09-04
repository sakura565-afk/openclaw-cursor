"""Tests for prompts.templates loader and rendering."""

import unittest

from prompts.templates.loader import (
    PromptTemplate,
    list_templates,
    load_template,
    render_body,
    template_ids,
)


class TestPromptTemplates(unittest.TestCase):
    def test_list_templates_includes_all_ids(self) -> None:
        ids = {item["id"] for item in list_templates()}
        expected = {
            "bug_investigation",
            "code_review",
            "documentation_writing",
            "error_handling",
            "self_improvement_agent",
        }
        self.assertEqual(ids, expected)

    def test_template_ids_sorted(self) -> None:
        ids = template_ids()
        self.assertEqual(ids, tuple(sorted(ids)))

    def test_load_each_template(self) -> None:
        for tid in template_ids():
            t = load_template(tid)
            self.assertIsInstance(t, PromptTemplate)
            self.assertEqual(t.id, tid)
            self.assertTrue(t.title)
            self.assertTrue(t.body.strip())
            self.assertTrue(t.usage.strip())

    def test_render_code_review(self) -> None:
        t = load_template("code_review")
        out = t.render(
            language_stack="Python 3.11",
            change_summary="Fix off-by-one in pagination",
            code_or_diff="def page(n): return range(n, n+10)",
            focus_areas="correctness, edge cases",
            project_conventions="PEP 8; type hints on public APIs",
        )
        self.assertIn("Python 3.11", out)
        self.assertIn("pagination", out)
        self.assertNotIn("{{", out)

    def test_render_body_missing_placeholder_strict(self) -> None:
        with self.assertRaises(KeyError):
            render_body("Hello {{ name }}", {}, strict=True)

    def test_render_body_strict_unknown_after_replace(self) -> None:
        with self.assertRaises(KeyError):
            render_body("{{ a }} {{ b }}", {"a": "1"}, strict=True)

    def test_render_body_non_strict(self) -> None:
        out = render_body("Hi {{ x }}", {}, strict=False)
        self.assertEqual(out, "Hi {{ x }}")


if __name__ == "__main__":
    unittest.main()
