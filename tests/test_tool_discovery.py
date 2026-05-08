"""Tests for the tool discovery self-improvement pipeline."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.self_improvement.tool_discovery import (
    CandidateKind,
    DiscoveryReport,
    ToolDiscoveryPipeline,
    analyze_python_file,
    format_report_markdown,
    parse_requirements_names,
    requirement_matches_import,
    top_level_module_name,
)


class ParseRequirementsTests(unittest.TestCase):
    def test_parses_basic_lines(self):
        with tempfile.TemporaryDirectory() as td:
            req = Path(td) / "requirements.txt"
            req.write_text(
                "flask>=2.0\n# comment\npython-frontmatter[extra]\n",
                encoding="utf-8",
            )
            names = parse_requirements_names(req)
            self.assertIn("flask", names)
            self.assertIn("python_frontmatter", names)


class RequirementMatchTests(unittest.TestCase):
    def test_alias_pillow(self):
        self.assertTrue(requirement_matches_import("pillow", "PIL"))


class AnalyzePythonTests(unittest.TestCase):
    def test_imports_top_level(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sample.py"
            p.write_text(
                "import os\nfrom urllib.parse import urlparse\n",
                encoding="utf-8",
            )
            data = analyze_python_file(p)
            self.assertIn("os", data["imports"])
            self.assertIn("urllib", data["imports"])

    def test_subprocess_literal_argv(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "run.py"
            p.write_text(
                "import subprocess\nsubprocess.run(['echo', 'hi'])\n",
                encoding="utf-8",
            )
            data = analyze_python_file(p)
            self.assertEqual(data["subprocess_commands"], [["echo", "hi"]])


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def test_dependency_gap_detected(self):
        (self.root / "requirements.txt").write_text("flask\n", encoding="utf-8")
        src = self.root / "src"
        src.mkdir()
        (src / "app.py").write_text("import numpy\n", encoding="utf-8")

        pipe = ToolDiscoveryPipeline(root_dir=self.root, scan_roots=("src",))
        report = pipe.run()
        kinds = [c.kind for c in report.candidates]
        self.assertIn(CandidateKind.DEPENDENCY_GAP, kinds)
        titles = " ".join(c.title for c in report.candidates)
        self.assertIn("numpy", titles)

    def test_missing_binary_candidate(self):
        (self.root / "requirements.txt").write_text("", encoding="utf-8")
        src = self.root / "src"
        src.mkdir()
        (src / "run.py").write_text(
            "import subprocess\nsubprocess.run(['missing_binary_unique_xyz', '-v'])\n",
            encoding="utf-8",
        )

        def fake_which(name):
            return None if name == "missing_binary_unique_xyz" else "/usr/bin/" + name

        pipe = ToolDiscoveryPipeline(
            root_dir=self.root,
            scan_roots=("src",),
            which_fn=fake_which,
            import_probe=lambda _: True,
        )
        report = pipe.run()
        missing = [c for c in report.candidates if c.kind == CandidateKind.MISSING_BINARY]
        self.assertTrue(any("missing_binary_unique_xyz" in m.title for m in missing))

    def test_save_report_writes_json(self):
        (self.root / "requirements.txt").write_text("flask\n", encoding="utf-8")
        logs = self.root / "logs"
        pipe = ToolDiscoveryPipeline(root_dir=self.root, scan_roots=())
        report = pipe.run()
        path = pipe.save_report(report, logs_dir=logs)
        self.assertTrue(path.exists())
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("candidates", payload)
        self.assertIn("summary", payload)

    def test_markdown_format_includes_title(self):
        report = DiscoveryReport(
            generated_at="2026-01-01T00:00:00+00:00",
            root_dir=str(self.root),
            candidates=[],
            stages=[],
            summary={"candidate_count": 0},
        )
        md = format_report_markdown(report)
        self.assertIn("Tool discovery report", md)


class TopLevelModuleTests(unittest.TestCase):
    def test_dotted(self):
        self.assertEqual(top_level_module_name("urllib.parse"), "urllib")
