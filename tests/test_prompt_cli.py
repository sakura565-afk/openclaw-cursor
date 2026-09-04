"""Tests for scripts.prompt_cli."""

from __future__ import annotations

import json
import tempfile
import textwrap
import unittest
from pathlib import Path

import yaml

from scripts import prompt_cli


def write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")


class PromptCliTestCase(unittest.TestCase):
    def test_substitute_strict_missing_raises(self) -> None:
        with self.assertRaises(KeyError):
            prompt_cli.substitute("Hello {name}", {}, strict=True)

    def test_substitute_strict_ok(self) -> None:
        out = prompt_cli.substitute("Hello {name}", {"name": "Ada"}, strict=True)
        self.assertEqual(out, "Hello Ada")

    def test_substitute_loose_preserves_unknown(self) -> None:
        out = prompt_cli.substitute("Hi {name}, ref {id}", {"name": "Ada"}, strict=False)
        self.assertEqual(out, "Hi Ada, ref {id}")

    def test_load_template_document_yaml_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            f = root / "t.yaml"
            write(
                f,
                """
                metadata:
                  description: Test
                  author: me
                  version: "2"
                  tags: [a, b]
                template: |
                  Line {x}
                """,
            )
            doc = prompt_cli.load_template_document(f)
            self.assertEqual(doc["template"], "Line {x}\n")
            meta = prompt_cli.normalize_metadata(doc["metadata"])
            self.assertEqual(meta["description"], "Test")
            self.assertEqual(meta["tags"], ["a", "b"])

    def test_load_template_document_json_body_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            f = root / "t.json"
            f.write_text(
                json.dumps(
                    {
                        "metadata": {"description": "J", "tags": "one, two"},
                        "body": "K={k}",
                    }
                ),
                encoding="utf-8",
            )
            doc = prompt_cli.load_template_document(f)
            self.assertEqual(doc["template"], "K={k}")
            meta = prompt_cli.normalize_metadata(doc["metadata"])
            self.assertEqual(meta["tags"], ["one", "two"])

    def test_render_integration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompts = root / "prompts"
            prompts.mkdir()
            (prompts / "config.yaml").write_text(
                "templates_directory: templates\nstrict_substitution: true\n",
                encoding="utf-8",
            )
            tdir = prompts / "templates"
            tdir.mkdir()
            write(
                tdir / "hello.yaml",
                """
                metadata:
                  description: Hello template
                template: |
                  {greeting}, {name}!
                """,
            )
            cfg = prompts / "config.yaml"
            config = prompt_cli.load_config(cfg)
            templates_dir = prompt_cli.resolve_templates_dir(cfg, config)
            doc = prompt_cli.load_template_document(tdir / "hello.yaml")
            rendered = prompt_cli.substitute(
                doc["template"],
                {"greeting": "Hello", "name": "World"},
                strict=True,
            )
            self.assertEqual(rendered.strip(), "Hello, World!")

    def test_parse_assignments(self) -> None:
        self.assertEqual(
            prompt_cli.parse_assignments(["a=1", "b=two=three"]),
            {"a": "1", "b": "two=three"},
        )

    def test_load_vars_file_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "v.yaml"
            p.write_text("x: 1\ny: hello\n", encoding="utf-8")
            self.assertEqual(prompt_cli.load_vars_file(p), {"x": "1", "y": "hello"})


if __name__ == "__main__":
    unittest.main()
