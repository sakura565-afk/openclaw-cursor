#!/usr/bin/env python3
"""Automated markdown documentation generator for OpenClaw scripts.

This utility scans the scripts/ directory, parses Python files with the ast
module, and generates:

* One README.md per script under docs/scripts/<script-name>/README.md
* A master index at docs/README.md

It is designed for both manual execution and pre-commit integration:

    python scripts/doc_generator.py
    python scripts/doc_generator.py --check

Suggested pre-commit hook:

    - repo: local
      hooks:
        - id: doc-generator
          name: Generate script documentation
          entry: python scripts/doc_generator.py --check
          language: system
          pass_filenames: false
"""

from __future__ import annotations

import argparse
import ast
import os
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence


GENERATED_START = "<!-- doc-generator:start -->"
GENERATED_END = "<!-- doc-generator:end -->"


class Ansi:
    RESET = "\033[0m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    CYAN = "\033[36m"


@dataclass(frozen=True)
class ArgumentDoc:
    names: tuple[str, ...]
    positional: bool
    help_text: str = ""
    required: bool = False
    default: str | None = None
    action: str | None = None
    value_type: str | None = None
    nargs: str | None = None
    choices: tuple[str, ...] = ()
    metavar: str | None = None
    dest: str | None = None

    @property
    def display_name(self) -> str:
        if self.positional:
            return self.names[0]
        long_options = [name for name in self.names if name.startswith("--")]
        return ", ".join(long_options or self.names)

    def usage_token(self) -> str:
        primary = self.display_name
        if self.positional:
            token = self.metavar or self.dest or primary.upper()
            return self._apply_nargs(token, wrap_optional=self.nargs in {"?", "*"})

        if self.action in {"store_true", "store_false", "count"}:
            token = primary
        else:
            value_name = self.metavar or self.dest or primary.lstrip("-").replace("-", "_").upper()
            token = f"{primary} {value_name}"
            token = self._apply_nargs(token, wrap_optional=False)

        if self.required:
            return token
        return f"[{token}]"

    def detail_text(self) -> str:
        details: list[str] = []
        if self.help_text:
            details.append(self.help_text)
        if self.value_type:
            details.append(f"Type: `{self.value_type}`.")
        if self.action and self.action not in {"store", "store_true", "store_false"}:
            details.append(f"Action: `{self.action}`.")
        if self.nargs:
            details.append(f"Nargs: `{self.nargs}`.")
        if self.choices:
            details.append("Choices: " + ", ".join(f"`{choice}`" for choice in self.choices) + ".")
        return " ".join(details) or "No description available."

    def _apply_nargs(self, token: str, wrap_optional: bool) -> str:
        if self.nargs in {"+", "*"}:
            token = f"{token} ..."
        if wrap_optional:
            return f"[{token}]"
        return token


@dataclass(frozen=True)
class ScriptDoc:
    source_path: Path
    relative_source: Path
    readme_path: Path
    summary: str
    description: str
    arguments: tuple[ArgumentDoc, ...] = field(default_factory=tuple)
    exit_codes: tuple[int, ...] = (0,)


def detect_color_enabled(no_color: bool) -> bool:
    return not no_color and not os.getenv("NO_COLOR")


def colorize(message: str, color: str, enabled: bool) -> str:
    if not enabled:
        return message
    return f"{color}{message}{Ansi.RESET}"


def log(message: str, color: str, *, enabled: bool) -> None:
    print(colorize(message, color, enabled), file=sys.stderr)


def markdown_escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


def extract_leading_header(existing_text: str) -> str:
    if not existing_text.strip():
        return ""

    lines = existing_text.splitlines()
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index >= len(lines) or not lines[index].lstrip().startswith("#"):
        return ""

    preserved: list[str] = []
    while index < len(lines):
        line = lines[index]
        if line.strip() == "" or line.lstrip().startswith("#"):
            preserved.append(line)
            index += 1
            continue
        break
    return "\n".join(preserved).strip()


def merge_generated_content(existing_text: str, default_header: str, generated_body: str) -> str:
    generated_block = f"{GENERATED_START}\n{generated_body.rstrip()}\n{GENERATED_END}\n"

    if GENERATED_START in existing_text and GENERATED_END in existing_text:
        before, _, tail = existing_text.partition(GENERATED_START)
        _, _, after = tail.partition(GENERATED_END)
        sections = [before.rstrip(), generated_block.rstrip(), after.strip()]
        return "\n\n".join(section for section in sections if section).rstrip() + "\n"

    header = extract_leading_header(existing_text) or default_header.strip()
    return f"{header.rstrip()}\n\n{generated_block}"


def safe_literal(node: ast.AST | None) -> object | None:
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (TypeError, ValueError, SyntaxError):
        return None


def expression_text(node: ast.AST | None) -> str | None:
    literal = safe_literal(node)
    if literal is not None:
        if isinstance(literal, (list, tuple, set)):
            return ", ".join(str(item) for item in literal)
        return str(literal)
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except AttributeError:
        return None


def qualified_name(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = qualified_name(node.value)
        if base:
            return f"{base}.{node.attr}"
    return None


def extract_target_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, (ast.Tuple, ast.List)):
        names: list[str] = []
        for element in node.elts:
            names.extend(extract_target_names(element))
        return names
    return []


class ScriptAnalyzer(ast.NodeVisitor):
    def __init__(self, module_docstring: str) -> None:
        self.module_docstring = module_docstring
        self.argparse_modules = {"argparse"}
        self.argument_parser_names = {"ArgumentParser"}
        self.parser_names: set[str] = set()
        self.arguments: list[ArgumentDoc] = []
        self.exit_codes: set[int] = {0}
        self.parser_description: str | None = None

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == "argparse":
                self.argparse_modules.add(alias.asname or alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "argparse":
            for alias in node.names:
                if alias.name == "ArgumentParser":
                    self.argument_parser_names.add(alias.asname or alias.name)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        self._record_parser_assignment(node.targets, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._record_parser_assignment([node.target], node.value)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        parsed_argument = self._parse_add_argument(node)
        if parsed_argument:
            self.arguments.append(parsed_argument)

        exit_code = self._parse_exit_call(node)
        if exit_code is not None:
            self.exit_codes.add(exit_code)

        self.generic_visit(node)

    def visit_Raise(self, node: ast.Raise) -> None:
        if isinstance(node.exc, ast.Call):
            exc_name = qualified_name(node.exc.func)
            if exc_name in {"SystemExit", "builtins.SystemExit"}:
                code = self._extract_int(node.exc.args[0]) if node.exc.args else 0
                if code is not None:
                    self.exit_codes.add(code)
        self.generic_visit(node)

    def _record_parser_assignment(self, targets: Sequence[ast.AST], value: ast.AST | None) -> None:
        if not isinstance(value, ast.Call):
            return
        if not self._is_argument_parser_call(value):
            return

        for target in targets:
            for name in extract_target_names(target):
                self.parser_names.add(name)

        for keyword in value.keywords:
            if keyword.arg == "description":
                description = expression_text(keyword.value)
                if description == "__doc__":
                    description = self.module_docstring
                if description:
                    self.parser_description = description

    def _is_argument_parser_call(self, call: ast.Call) -> bool:
        name = qualified_name(call.func)
        if not name:
            return False
        module_name, _, attr_name = name.rpartition(".")
        return (module_name in self.argparse_modules and attr_name == "ArgumentParser") or name in self.argument_parser_names

    def _parse_add_argument(self, call: ast.Call) -> ArgumentDoc | None:
        if not isinstance(call.func, ast.Attribute) or call.func.attr != "add_argument":
            return None
        if not isinstance(call.func.value, ast.Name) or call.func.value.id not in self.parser_names:
            return None

        raw_names = [expression_text(argument) for argument in call.args]
        names = tuple(name for name in raw_names if name)
        if not names:
            return None

        positional = not names[0].startswith("-")
        keyword_map = {keyword.arg: keyword.value for keyword in call.keywords if keyword.arg}

        choices_value = safe_literal(keyword_map.get("choices"))
        if isinstance(choices_value, (list, tuple, set)):
            choices = tuple(str(item) for item in choices_value)
        else:
            choices = ()

        action = expression_text(keyword_map.get("action"))
        default = expression_text(keyword_map.get("default"))
        value_type = expression_text(keyword_map.get("type"))
        nargs = expression_text(keyword_map.get("nargs"))
        help_text = expression_text(keyword_map.get("help")) or ""
        metavar = expression_text(keyword_map.get("metavar"))
        dest = expression_text(keyword_map.get("dest")) or self._derive_dest(names, positional)

        required_literal = safe_literal(keyword_map.get("required"))
        if required_literal is not None:
            required = bool(required_literal)
        elif positional:
            required = nargs not in {"?", "*"}
        else:
            required = False

        return ArgumentDoc(
            names=names,
            positional=positional,
            help_text=help_text,
            required=required,
            default=default,
            action=action,
            value_type=value_type,
            nargs=nargs,
            choices=choices,
            metavar=metavar,
            dest=dest,
        )

    def _derive_dest(self, names: tuple[str, ...], positional: bool) -> str:
        if positional:
            return names[0]
        for name in reversed(names):
            if name.startswith("--"):
                return name.lstrip("-").replace("-", "_")
        return names[0].lstrip("-").replace("-", "_")

    def _parse_exit_call(self, call: ast.Call) -> int | None:
        name = qualified_name(call.func)
        if name in {"sys.exit", "exit"}:
            return self._extract_int(call.args[0]) if call.args else 0
        if isinstance(call.func, ast.Attribute) and call.func.attr == "exit":
            if isinstance(call.func.value, ast.Name) and call.func.value.id in self.parser_names:
                return self._extract_int(call.args[0]) if call.args else 0
        return None

    def _extract_int(self, node: ast.AST | None) -> int | None:
        literal = safe_literal(node)
        if isinstance(literal, int) and not isinstance(literal, bool):
            return literal
        return None


def summarize_description(module_docstring: str, parser_description: str | None) -> tuple[str, str]:
    description = (module_docstring or parser_description or "No module docstring was found.").strip()
    if parser_description and parser_description.strip() and parser_description.strip() not in description:
        description = f"{description}\n\nCLI description: {parser_description.strip()}"
    summary = next((line.strip() for line in description.splitlines() if line.strip()), "No description available.")
    return summary, description


def parse_script(source_path: Path, scripts_dir: Path, docs_dir: Path) -> ScriptDoc:
    source_text = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source_text, filename=str(source_path))
    module_docstring = ast.get_docstring(tree) or ""
    analyzer = ScriptAnalyzer(module_docstring=module_docstring)
    analyzer.visit(tree)
    summary, description = summarize_description(module_docstring, analyzer.parser_description)

    relative_source = source_path.relative_to(scripts_dir.parent)
    doc_relative = source_path.relative_to(scripts_dir).with_suffix("")
    readme_path = docs_dir / "scripts" / doc_relative / "README.md"

    return ScriptDoc(
        source_path=source_path,
        relative_source=relative_source,
        readme_path=readme_path,
        summary=summary,
        description=description,
        arguments=tuple(analyzer.arguments),
        exit_codes=tuple(sorted(analyzer.exit_codes)),
    )


def build_usage_examples(script_doc: ScriptDoc) -> list[str]:
    base_command = f"python {script_doc.relative_source.as_posix()}"
    examples: list[str] = [f"{base_command} --help"]

    required_tokens: list[str] = []
    optional_example_token: str | None = None
    for argument in script_doc.arguments:
        token = argument.usage_token()
        if argument.positional or argument.required:
            required_tokens.append(token.strip("[]"))
        elif optional_example_token is None:
            optional_example_token = token.strip("[]")

    examples.append(" ".join(part for part in [base_command, *required_tokens] if part).strip())
    if optional_example_token:
        examples.append(" ".join(part for part in [base_command, *required_tokens, optional_example_token] if part).strip())

    unique_examples: list[str] = []
    for example in examples:
        if example and example not in unique_examples:
            unique_examples.append(example)
    return unique_examples


def render_argument_table(arguments: Sequence[ArgumentDoc]) -> str:
    if not arguments:
        return "No CLI arguments detected.\n"

    lines = [
        "| Argument | Required | Default | Details |",
        "| --- | --- | --- | --- |",
    ]
    for argument in arguments:
        default_text = argument.default if argument.default is not None else "-"
        lines.append(
            "| {name} | {required} | {default} | {details} |".format(
                name=markdown_escape(argument.display_name),
                required="Yes" if argument.required else "No",
                default=markdown_escape(default_text),
                details=markdown_escape(argument.detail_text()),
            )
        )
    return "\n".join(lines) + "\n"


def render_exit_codes(exit_codes: Sequence[int]) -> str:
    lines = [
        "| Code | Meaning |",
        "| --- | --- |",
    ]
    for code in exit_codes:
        if code == 0:
            meaning = "Successful execution."
        else:
            meaning = "Explicit non-zero exit detected in source."
        lines.append(f"| {code} | {meaning} |")
    return "\n".join(lines) + "\n"


def render_script_readme(script_doc: ScriptDoc) -> str:
    usage_block = "\n".join(build_usage_examples(script_doc))
    sections = [
        "## Source",
        f"`{script_doc.relative_source.as_posix()}`",
        "## Description",
        script_doc.description.strip(),
        "## Usage examples",
        f"```bash\n{usage_block}\n```",
        "## Arguments",
        render_argument_table(script_doc.arguments).rstrip(),
        "## Exit codes",
        render_exit_codes(script_doc.exit_codes).rstrip(),
    ]
    body = "\n\n".join(section for section in sections if section).strip()

    existing_text = script_doc.readme_path.read_text(encoding="utf-8") if script_doc.readme_path.exists() else ""
    default_header = f"# {script_doc.relative_source.name}"
    return merge_generated_content(existing_text, default_header, body)


def render_master_index(script_docs: Sequence[ScriptDoc], docs_dir: Path) -> str:
    rows = [
        "| Script | Summary | Source |",
        "| --- | --- | --- |",
    ]
    for script_doc in script_docs:
        relative_readme = script_doc.readme_path.relative_to(docs_dir).as_posix()
        rows.append(
            "| [{name}]({link}) | {summary} | `{source}` |".format(
                name=markdown_escape(script_doc.relative_source.name),
                link=relative_readme,
                summary=markdown_escape(script_doc.summary),
                source=script_doc.relative_source.as_posix(),
            )
        )
    index_rows = "\n".join(rows)

    body = textwrap.dedent(
        """\
        ## How to run
        Manually:

        ```bash
        python scripts/doc_generator.py
        ```

        With a pre-commit hook:

        ```yaml
        - repo: local
          hooks:
            - id: doc-generator
              name: Generate script documentation
              entry: python scripts/doc_generator.py --check
              language: system
              pass_filenames: false
        ```

        ## Script index
        {rows}
        """
    ).format(rows=index_rows).strip()

    index_path = docs_dir / "README.md"
    existing_text = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
    return merge_generated_content(existing_text, "# Script Documentation", body)


def discover_scripts(root_dir: Path, scripts_dir: Path, filenames: Sequence[str]) -> list[Path]:
    discovered = sorted(
        path
        for path in scripts_dir.rglob("*.py")
        if path.is_file() and not path.name.startswith("__")
    )

    if not filenames:
        return discovered

    selected: set[Path] = set()
    for filename in filenames:
        candidate = (root_dir / filename).resolve()
        if candidate.exists() and candidate.suffix == ".py" and scripts_dir in candidate.parents:
            selected.add(candidate)

    if not selected:
        return discovered
    return [path for path in discovered if path.resolve() in selected]


def write_if_changed(path: Path, content: str, *, check: bool) -> bool:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if existing == content:
        return False
    if not check:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return True


def generate_docs(root_dir: Path, scripts_dir: Path, docs_dir: Path, *, check: bool, filenames: Sequence[str], color: bool) -> int:
    if not scripts_dir.exists():
        log(f"Scripts directory does not exist: {scripts_dir}", Ansi.RED, enabled=color)
        return 2

    scripts = discover_scripts(root_dir, scripts_dir, filenames)
    script_docs = [parse_script(path, scripts_dir, docs_dir) for path in scripts]

    changed_files: list[Path] = []
    for script_doc in script_docs:
        rendered = render_script_readme(script_doc)
        if write_if_changed(script_doc.readme_path, rendered, check=check):
            changed_files.append(script_doc.readme_path)

    index_content = render_master_index(script_docs, docs_dir)
    index_path = docs_dir / "README.md"
    if write_if_changed(index_path, index_content, check=check):
        changed_files.append(index_path)

    if changed_files:
        status_color = Ansi.YELLOW if check else Ansi.GREEN
        verb = "would update" if check else "updated"
        for changed_file in changed_files:
            log(f"{verb}: {changed_file.relative_to(root_dir)}", status_color, enabled=color)
        return 1 if check else 0

    log("Documentation is up to date.", Ansi.BLUE, enabled=color)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate markdown documentation for scripts.")
    parser.add_argument(
        "filenames",
        nargs="*",
        help="Optional filenames from pre-commit; matching scripts are regenerated.",
    )
    parser.add_argument(
        "--scripts-dir",
        default="scripts",
        help="Directory containing Python scripts to scan.",
    )
    parser.add_argument(
        "--docs-dir",
        default="docs",
        help="Directory where generated markdown files are written.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check whether generated documentation is up to date without writing files.",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI color output.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    root_dir = Path.cwd()
    scripts_dir = (root_dir / args.scripts_dir).resolve()
    docs_dir = (root_dir / args.docs_dir).resolve()
    color = detect_color_enabled(args.no_color)

    try:
        return generate_docs(
            root_dir=root_dir.resolve(),
            scripts_dir=scripts_dir,
            docs_dir=docs_dir,
            check=args.check,
            filenames=args.filenames,
            color=color,
        )
    except SyntaxError as error:
        log(f"Failed to parse Python source: {error}", Ansi.RED, enabled=color)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
