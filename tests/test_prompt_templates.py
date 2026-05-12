from __future__ import annotations

import io
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.prompts import renderer  # noqa: E402
from scripts.prompts import cli as prompts_cli  # noqa: E402


class ParseVarAssignmentsTests(unittest.TestCase):
    def test_splits_on_first_equals(self) -> None:
        out = renderer.parse_var_assignments(
            ["bug_description=NULL pointer crash", "severity=high"]
        )
        self.assertEqual(out["bug_description"], "NULL pointer crash")
        self.assertEqual(out["severity"], "high")

    def test_value_may_contain_equals(self) -> None:
        out = renderer.parse_var_assignments(["a=b=c"])
        self.assertEqual(out["a"], "b=c")

    def test_strips_whitespace(self) -> None:
        out = renderer.parse_var_assignments(["  key  =  value  "])
        self.assertEqual(out["key"], "value")

    def test_invalid_raises(self) -> None:
        with self.assertRaises(ValueError):
            renderer.parse_var_assignments(["noequals"])


class RenderTemplateTests(unittest.TestCase):
    def test_replaces_placeholders(self) -> None:
        text = "Hello {{name}}, count={{n}}"
        out, missing = renderer.render_template(text, {"name": "Ada", "n": "2"})
        self.assertEqual(out, "Hello Ada, count=2")
        self.assertEqual(missing, [])

    def test_leave_missing_default(self) -> None:
        text = "A {{x}} B {{y}}"
        out, missing = renderer.render_template(text, {"x": "1"})
        self.assertEqual(out, "A 1 B {{y}}")
        self.assertEqual(missing, ["y"])

    def test_empty_missing(self) -> None:
        text = "A {{x}} B {{y}}"
        out, missing = renderer.render_template(text, {"x": "1"}, leave_missing=False)
        self.assertEqual(out, "A 1 B ")
        self.assertEqual(missing, [])


class TemplatePathTests(unittest.TestCase):
    def test_list_and_load_shipped_templates(self) -> None:
        package = ROOT / "scripts" / "prompts"
        names = renderer.list_template_names(package)
        self.assertIn("bug_hunt", names)
        self.assertIn("code_review", names)
        self.assertNotIn("INDEX", names)
        body = renderer.load_template("bug_hunt", package)
        self.assertIn("{{bug_description}}", body)
        self.assertIn("{{severity}}", body)

    def test_load_missing_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            renderer.load_template("does_not_exist_zzz", ROOT / "scripts" / "prompts")


class IsolatedTemplatesDirTests(unittest.TestCase):
    def test_round_trip_custom_package_root(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            pkg = Path(raw) / "pkg"
            (pkg / "templates").mkdir(parents=True)
            (pkg / "templates" / "demo.md").write_text(
                "V={{v}}\n", encoding="utf-8"
            )
            names = renderer.list_template_names(pkg)
            self.assertEqual(names, ["demo"])
            content = renderer.load_template("demo", pkg)
            rendered, missing = renderer.render_template(content, {"v": "ok"})
            self.assertEqual(rendered, "V=ok\n")
            self.assertEqual(missing, [])


class CliMainTests(unittest.TestCase):
    def test_list_command(self) -> None:
        buf = io.StringIO()
        with mock.patch.object(sys, "stdout", buf):
            rc = prompts_cli.main(["list"])
        self.assertEqual(rc, 0)
        lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
        self.assertIn("bug_hunt", lines)

    def test_render_bug_hunt_partial_vars(self) -> None:
        buf = io.StringIO()
        err = io.StringIO()
        with mock.patch.object(sys, "stdout", buf), mock.patch.object(sys, "stderr", err):
            rc = prompts_cli.main(
                [
                    "render",
                    "bug_hunt",
                    "--var",
                    "bug_description=NULL pointer crash",
                    "--var",
                    "severity=high",
                ]
            )
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("NULL pointer crash", out)
        self.assertIn("high", out)
        self.assertIn("missing values", err.getvalue().lower())

    def test_render_code_review_strict_missing(self) -> None:
        err = io.StringIO()
        with mock.patch.object(sys, "stdout", io.StringIO()), mock.patch.object(
            sys, "stderr", err
        ):
            rc = prompts_cli.main(
                [
                    "render",
                    "code_review",
                    "--strict",
                    "--var",
                    "review_scope=src/foo.py",
                ]
            )
        self.assertEqual(rc, 3)
        self.assertIn("Missing values", err.getvalue())

    def test_render_writes_output_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            out_path = Path(raw) / "out.md"
            rc = prompts_cli.main(
                [
                    "render",
                    "step_by_step",
                    "-o",
                    str(out_path),
                    "--empty-missing",
                    "--var",
                    "task_title=Deploy hotfix",
                    "--var",
                    "starting_point=main is green",
                    "--var",
                    "tools=kubectl",
                    "--var",
                    "success_criteria=pods healthy",
                    "--var",
                    "safety_notes=read-only dry run first",
                ]
            )
            self.assertEqual(rc, 0)
            text = out_path.read_text(encoding="utf-8")
            self.assertIn("Deploy hotfix", text)
            self.assertNotIn("{{task_title}}", text)

    def test_invalid_var_exits_2(self) -> None:
        err = io.StringIO()
        with mock.patch.object(sys, "stdout", io.StringIO()), mock.patch.object(
            sys, "stderr", err
        ):
            rc = prompts_cli.main(["render", "bug_hunt", "--var", "notvalid"])
        self.assertEqual(rc, 2)


class SubprocessCliTests(unittest.TestCase):
    def test_cli_list_subprocess(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "prompts" / "cli.py"), "list"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("error_debug", proc.stdout)

    def test_cli_render_error_debug_subprocess(self) -> None:
        proc = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "prompts" / "cli.py"),
                "render",
                "error_debug",
                "--var",
                "error_signature=TypeError: NoneType",
                "--var",
                "command_or_context=pytest tests/test_x.py",
                "--var",
                "logs_excerpt=File \"t.py\", line 1",
                "--var",
                "expected_behavior=tests pass",
                "--var",
                "recent_changes=none",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("TypeError: NoneType", proc.stdout)
        self.assertNotIn("{{error_signature}}", proc.stdout)

    def test_cli_render_research_synthesis_subprocess(self) -> None:
        proc = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "prompts" / "cli.py"),
                "render",
                "research_synthesis",
                "--strict",
                "--var",
                "topic=Adopt vector DB for embeddings?",
                "--var",
                "audience=Staff engineers",
                "--var",
                "sources=internal ADR-42, vendor docs",
                "--var",
                "deliverable_shape=1-page memo + risks table",
                "--var",
                "time_horizon=2024–2026 primary sources",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("vector DB", proc.stdout)
        self.assertNotIn("{{topic}}", proc.stdout)


if __name__ == "__main__":
    unittest.main()
