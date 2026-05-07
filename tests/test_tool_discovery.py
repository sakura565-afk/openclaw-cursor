from __future__ import annotations

import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.tool_discovery import (  # noqa: E402
    Parameter,
    Tool,
    discover,
    export_json,
    export_markdown,
    export_summary,
    main,
    scan_mcp_servers,
    scan_scripts,
    scan_skills,
    search_registry,
    _parse_yaml_subset,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8")


class FrontmatterParserTests(unittest.TestCase):
    def test_parses_nested_lists_and_scalars(self) -> None:
        raw = textwrap.dedent(
            """
            name: image-toolbox
            description: |
              Multi-line
              description.
            tags: [image, render]
            parameters:
              - name: input
                type: path
                required: true
                description: Source image.
              - name: operation
                choices: [resize, upscale]
            """
        )
        parsed = _parse_yaml_subset(raw)
        self.assertEqual(parsed["name"], "image-toolbox")
        self.assertEqual(parsed["tags"], ["image", "render"])
        self.assertIn("Multi-line", parsed["description"])
        params = parsed["parameters"]
        self.assertEqual(params[0]["name"], "input")
        self.assertTrue(params[0]["required"])
        self.assertEqual(params[1]["choices"], ["resize", "upscale"])


class SkillScannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_handles_missing_root(self) -> None:
        self.assertEqual(scan_skills(self.root / "missing"), [])
        self.assertEqual(scan_skills(None), [])

    def test_parses_skill_with_frontmatter_and_sections(self) -> None:
        skill_dir = self.root / "image-toolbox"
        _write(
            skill_dir / "SKILL.md",
            """
            ---
            name: image-toolbox
            description: Multi-purpose image manipulation skill.
            tags: [image, render]
            parameters:
              - name: input
                type: path
                required: true
                description: Source image path.
              - name: operation
                type: string
                required: true
                choices: [resize, upscale]
            use_cases:
              - Quickly upscale images.
            version: 1.0.0
            ---

            # Image toolbox

            ## When to use
            - You need to upscale.
            - You need to restyle.
            """,
        )

        skills = scan_skills(self.root)
        self.assertEqual(len(skills), 1)
        skill = skills[0]
        self.assertEqual(skill.id, "skill:image-toolbox")
        self.assertEqual(skill.kind, "skill")
        self.assertEqual({p.name for p in skill.parameters}, {"input", "operation"})
        input_param = next(p for p in skill.parameters if p.name == "input")
        self.assertTrue(input_param.required)
        self.assertEqual(input_param.type, "path")
        self.assertIn("image", skill.tags)
        self.assertIn("Quickly upscale images.", skill.use_cases)
        self.assertEqual(skill.metadata["version"], "1.0.0")
        self.assertEqual(skill.parse_warnings, [])

    def test_falls_back_to_markdown_when_no_frontmatter(self) -> None:
        skill_dir = self.root / "text-helper"
        _write(
            skill_dir / "SKILL.md",
            """
            # Text helper

            Summarizes long text quickly.

            ## Parameters
            - `input`: Path to input file.
            - `model`: Ollama model to use.

            ## Use cases
            - Daily standup summaries.
            """,
        )

        skill = scan_skills(self.root)[0]
        self.assertEqual(skill.id, "skill:text-helper")
        self.assertIn("Summarizes long text", skill.description)
        self.assertEqual({p.name for p in skill.parameters}, {"input", "model"})
        self.assertIn("Daily standup summaries.", skill.use_cases)

    def test_merges_sidecar_when_frontmatter_missing_fields(self) -> None:
        skill_dir = self.root / "vision"
        _write(skill_dir / "SKILL.md", "# Vision skill\n\nVision helpers.\n")
        _write(
            skill_dir / "skill.json",
            json.dumps({"name": "vision", "tags": ["image", "vision"], "version": "2.0"}),
        )

        skill = scan_skills(self.root)[0]
        self.assertEqual(skill.tags, ["image", "vision"])
        self.assertEqual(skill.metadata["version"], "2.0")


class ScriptScannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_extracts_docstring_and_argparse(self) -> None:
        _write(
            self.root / "demo.py",
            '''
            """Demonstration CLI for tool discovery tests.

            Performs an example operation.
            """

            import argparse


            def main() -> int:
                parser = argparse.ArgumentParser()
                parser.add_argument("input", help="Input file path.")
                parser.add_argument("--mode", choices=["fast", "slow"], default="fast", help="Run mode.")
                parser.add_argument("--verbose", action="store_true", help="Verbose output.")
                parser.parse_args()
                return 0
            ''',
        )

        scripts = scan_scripts(self.root)
        self.assertEqual(len(scripts), 1)
        script = scripts[0]
        self.assertEqual(script.id, "script:demo")
        self.assertEqual(script.language, "python")
        self.assertIn("Demonstration CLI", script.description)
        params = {p.name: p for p in script.parameters}
        self.assertIn("input", params)
        self.assertTrue(params["input"].required)
        self.assertEqual(params["mode"].choices, ["fast", "slow"])
        self.assertEqual(params["verbose"].type, "bool")

    def test_skips_init_and_handles_syntax_errors(self) -> None:
        _write(self.root / "__init__.py", "")
        _write(self.root / "broken.py", "def main(:\n    pass\n")
        scripts = scan_scripts(self.root)
        names = {s.name for s in scripts}
        self.assertEqual(names, {"broken"})
        broken = next(s for s in scripts if s.name == "broken")
        self.assertTrue(any("python parse error" in w for w in broken.parse_warnings))

    def test_shell_leading_comment_block(self) -> None:
        _write(
            self.root / "deploy.sh",
            """
            #!/usr/bin/env bash
            # Deploy the application.
            # Reads SECRETS from env.

            set -euo pipefail
            echo deploying
            """,
        )

        scripts = scan_scripts(self.root)
        self.assertEqual(len(scripts), 1)
        self.assertEqual(scripts[0].language, "shell")
        self.assertIn("Deploy the application.", scripts[0].description)


class McpScannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_parses_mcp_servers_json(self) -> None:
        _write(
            self.root / "mcp_servers.json",
            json.dumps(
                {
                    "mcpServers": {
                        "github": {
                            "command": "npx",
                            "args": ["-y", "@modelcontextprotocol/server-github"],
                            "env": {"GITHUB_TOKEN": "token"},
                            "description": "GitHub access.",
                        }
                    }
                }
            ),
        )

        servers = scan_mcp_servers(self.root)
        self.assertEqual(len(servers), 1)
        server = servers[0]
        self.assertEqual(server.id, "mcp:github")
        self.assertEqual(server.metadata["command"], "npx")
        self.assertIn("mcp", server.tags)
        env_param = next(p for p in server.parameters if p.name == "GITHUB_TOKEN")
        self.assertEqual(env_param.type, "env")

    def test_scans_per_server_directories(self) -> None:
        _write(
            self.root / "filesystem" / "mcp.json",
            json.dumps(
                {
                    "name": "filesystem",
                    "command": "uvx",
                    "args": ["mcp-server-filesystem"],
                    "description": "Filesystem MCP server.",
                    "tags": ["fs"],
                }
            ),
        )

        servers = scan_mcp_servers(self.root)
        self.assertEqual([s.id for s in servers], ["mcp:filesystem"])
        self.assertIn("fs", servers[0].tags)
        self.assertIn("mcp", servers[0].tags)

    def test_invalid_json_is_recorded_as_warning(self) -> None:
        _write(self.root / "mcp.json", "{not valid")
        servers = scan_mcp_servers(self.root)
        self.assertEqual(len(servers), 1)
        self.assertTrue(servers[0].parse_warnings)


class RegistryAndExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.skills_dir = root / "skills"
        self.scripts_dir = root / "scripts"
        self.mcp_dir = root / "mcp"

        _write(
            self.skills_dir / "image-toolbox" / "SKILL.md",
            """
            ---
            name: image-toolbox
            description: Image manipulation skill.
            tags: [image]
            ---
            """,
        )
        _write(
            self.scripts_dir / "demo.py",
            '''
            """Demo script for registry tests."""
            import argparse
            argparse.ArgumentParser().add_argument("path")
            ''',
        )
        _write(
            self.mcp_dir / "mcp_servers.json",
            json.dumps(
                {
                    "mcpServers": {
                        "memory": {
                            "command": "npx",
                            "args": ["mcp-memory"],
                            "description": "Persistent memory.",
                        }
                    }
                }
            ),
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _build(self):
        return discover(self.skills_dir, self.scripts_dir, self.mcp_dir)

    def test_discover_aggregates_all_kinds(self) -> None:
        registry = self._build()
        counts = registry.counts()
        self.assertEqual(counts["skill"], 1)
        self.assertEqual(counts["script"], 1)
        self.assertEqual(counts["mcp_server"], 1)
        self.assertEqual(counts["total"], 3)

    def test_search_ranks_relevant_tools_first(self) -> None:
        registry = self._build()
        results = search_registry(registry, "image")
        self.assertTrue(results)
        self.assertEqual(results[0][0].id, "skill:image-toolbox")

    def test_export_json_round_trips(self) -> None:
        registry = self._build()
        payload = json.loads(export_json(registry))
        self.assertEqual(payload["counts"]["total"], 3)
        ids = {tool["id"] for tool in payload["tools"]}
        self.assertIn("skill:image-toolbox", ids)
        self.assertIn("mcp:memory", ids)

    def test_export_markdown_contains_section_headers(self) -> None:
        markdown = export_markdown(self._build())
        self.assertIn("# OpenClaw Tool Registry", markdown)
        self.assertIn("## Skills", markdown)
        self.assertIn("## Scripts", markdown)
        self.assertIn("## MCP Servers", markdown)

    def test_export_summary_is_compact(self) -> None:
        summary = export_summary(self._build())
        self.assertIn("OpenClaw tool registry", summary)
        self.assertIn("[skill] skill:image-toolbox", summary)

    def test_cli_summary_writes_to_output_file(self) -> None:
        out = Path(self.tmp.name) / "out.json"
        rc = main(
            [
                "--skills-dir",
                str(self.skills_dir),
                "--scripts-dir",
                str(self.scripts_dir),
                "--mcp-dir",
                str(self.mcp_dir),
                "summary",
                "--format",
                "json",
                "--output",
                str(out),
            ]
        )
        self.assertEqual(rc, 0)
        payload = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(payload["counts"]["total"], 3)

    def test_cli_show_unknown_id_returns_error(self) -> None:
        rc = main(
            [
                "--skills-dir",
                str(self.skills_dir),
                "--scripts-dir",
                str(self.scripts_dir),
                "--mcp-dir",
                str(self.mcp_dir),
                "show",
                "skill:does-not-exist",
            ]
        )
        self.assertEqual(rc, 2)


class ToolModelTests(unittest.TestCase):
    def test_search_text_includes_all_searchable_fields(self) -> None:
        tool = Tool(
            id="skill:foo",
            kind="skill",
            name="Foo",
            source_path="/tmp/foo",
            description="A foo skill.",
            summary="A foo skill.",
            parameters=[Parameter(name="input", description="Input file.")],
            use_cases=["Compute foo."],
            tags=["alpha"],
        )
        text = tool.search_text()
        for needle in ("skill:foo", "foo skill", "input file", "compute foo", "alpha"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
