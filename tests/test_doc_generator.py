import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from scripts import doc_generator


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")


def sample_script() -> str:
    return """
    \"\"\"Generate OpenClaw data documentation.

    Export metadata and validate generated manifests.
    \"\"\"

    import argparse
    import sys


    def build_parser():
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("input_path", help="Input asset directory.")
        parser.add_argument("--format", choices=["json", "yaml"], default="json", help="Output format.")
        parser.add_argument("--verbose", action="store_true", help="Enable verbose logging.")
        parser.add_argument("--retry", type=int, required=True, help="Retry count.")
        return parser


    def main():
        parser = build_parser()
        args = parser.parse_args()
        if args.retry < 0:
            sys.exit(3)
        return 0


    if __name__ == "__main__":
        raise SystemExit(main())
    """


class DocGeneratorTestCase(unittest.TestCase):
    def test_parse_script_extracts_docstrings_arguments_and_exit_codes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            scripts_dir = tmp_path / "scripts"
            docs_dir = tmp_path / "docs"
            script_path = scripts_dir / "export_assets.py"
            write_file(script_path, sample_script())

            script_doc = doc_generator.parse_script(script_path, scripts_dir, docs_dir)

            self.assertEqual(script_doc.summary, "Generate OpenClaw data documentation.")
            self.assertIn("Export metadata and validate generated manifests.", script_doc.description)
            self.assertEqual(script_doc.relative_source.as_posix(), "scripts/export_assets.py")
            self.assertEqual(script_doc.exit_codes, (0, 3))

            arguments = {argument.display_name: argument for argument in script_doc.arguments}
            self.assertIn("input_path", arguments)
            self.assertIn("--format", arguments)
            self.assertIn("--verbose", arguments)
            self.assertIn("--retry", arguments)
            self.assertEqual(arguments["--format"].choices, ("json", "yaml"))
            self.assertTrue(arguments["--retry"].required)
            self.assertEqual(arguments["--retry"].value_type, "int")

    def test_generate_docs_writes_markdown_and_preserves_existing_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            scripts_dir = tmp_path / "scripts"
            docs_dir = tmp_path / "docs"
            script_path = scripts_dir / "export_assets.py"
            write_file(script_path, sample_script())

            existing_readme = docs_dir / "scripts" / "export_assets" / "README.md"
            write_file(
                existing_readme,
                """
                # Custom Export Assets Guide

                ## Maintainer Notes
                """,
            )

            exit_code = doc_generator.generate_docs(
                root_dir=tmp_path,
                scripts_dir=scripts_dir,
                docs_dir=docs_dir,
                check=False,
                filenames=[],
                color=False,
            )

            self.assertEqual(exit_code, 0)

            script_readme = existing_readme.read_text(encoding="utf-8")
            self.assertTrue(script_readme.startswith("# Custom Export Assets Guide"))
            self.assertIn("## Usage examples", script_readme)
            self.assertIn("python scripts/export_assets.py --help", script_readme)
            self.assertIn("| --retry | Yes | - | Retry count. Type: `int`. |", script_readme)
            self.assertIn("| 3 | Explicit non-zero exit detected in source. |", script_readme)
            self.assertIn(doc_generator.GENERATED_START, script_readme)
            self.assertIn(doc_generator.GENERATED_END, script_readme)

            index_text = (docs_dir / "README.md").read_text(encoding="utf-8")
            self.assertIn("# Script Documentation", index_text)
            self.assertIn("[export_assets.py](scripts/export_assets/README.md)", index_text)
            self.assertIn("python scripts/doc_generator.py", index_text)
            self.assertIn("entry: python scripts/doc_generator.py --check", index_text)

    def test_check_mode_detects_stale_output_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            scripts_dir = tmp_path / "scripts"
            docs_dir = tmp_path / "docs"
            script_path = scripts_dir / "export_assets.py"
            write_file(script_path, sample_script())

            stale_readme = docs_dir / "scripts" / "export_assets" / "README.md"
            write_file(stale_readme, "# Export Assets\n\nOld text.\n")

            exit_code = doc_generator.generate_docs(
                root_dir=tmp_path,
                scripts_dir=scripts_dir,
                docs_dir=docs_dir,
                check=True,
                filenames=[],
                color=False,
            )

            self.assertEqual(exit_code, 1)
            self.assertEqual(stale_readme.read_text(encoding="utf-8"), "# Export Assets\n\nOld text.\n")
            self.assertFalse((docs_dir / "README.md").exists())

    def test_cli_manual_and_check_modes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            write_file(tmp_path / "scripts" / "export_assets.py", sample_script())

            run_result = subprocess.run(
                [sys.executable, "/workspace/scripts/doc_generator.py", "--no-color"],
                cwd=tmp_path,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(run_result.returncode, 0)
            self.assertIn("updated: docs/README.md", run_result.stderr)

            check_result = subprocess.run(
                [sys.executable, "/workspace/scripts/doc_generator.py", "--check", "--no-color"],
                cwd=tmp_path,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(check_result.returncode, 0)
            self.assertIn("Documentation is up to date.", check_result.stderr)


if __name__ == "__main__":
    unittest.main()
