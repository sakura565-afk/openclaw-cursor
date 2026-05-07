from __future__ import annotations

import argparse
import ast
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set


EXEC_CALLS: Set[str] = {
    "exec",
    "eval",
    "os.system",
    "subprocess.run",
    "subprocess.Popen",
}

WEB_CALLS: Set[str] = {
    "requests.request",
    "requests.get",
    "requests.post",
    "requests.put",
    "requests.delete",
    "requests.patch",
    "httpx.request",
    "httpx.get",
    "httpx.post",
    "urllib.request.urlopen",
    "urllib_request.urlopen",
    "urlopen",
    "aiohttp.ClientSession.get",
    "aiohttp.ClientSession.post",
}

FILE_CALLS: Set[str] = {
    "open",
    "os.open",
    "Path.open",
    "Path.read_text",
    "Path.write_text",
    "Path.read_bytes",
    "Path.write_bytes",
    "read_text",
    "write_text",
}

MESSAGE_METHODS: Set[str] = {
    "send",
    "send_message",
    "send_document",
    "send_photo",
    "send_media_group",
    "emit",
    "publish",
    "notify",
    "post_message",
    "send_text",
    "send_file",
    "send_payload",
    "send_update",
    "send_notification",
}

MESSAGE_PREFIXES: Sequence[str] = (
    "send_",
    "emit_",
    "publish_",
    "notify_",
)


@dataclass(frozen=True)
class UsageExample:
    file: str
    line: int
    call: str
    snippet: str


@dataclass
class ToolRecord:
    name: str
    module_path: str
    capabilities: Set[str] = field(default_factory=set)
    usage_examples: List[UsageExample] = field(default_factory=list)

    def add_example(self, example: UsageExample, max_examples: int) -> None:
        key = (example.file, example.line, example.call)
        existing = {(item.file, item.line, item.call) for item in self.usage_examples}
        if key in existing:
            return
        if len(self.usage_examples) < max_examples:
            self.usage_examples.append(example)

    def to_inventory_entry(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "module_path": self.module_path,
            "capabilities": sorted(self.capabilities),
            "usage_examples": [
                {
                    "file": example.file,
                    "line": example.line,
                    "call": example.call,
                    "snippet": example.snippet,
                }
                for example in self.usage_examples
            ],
        }


class _CallClassifier:
    def classify(self, call_name: str) -> Optional[str]:
        if call_name in EXEC_CALLS:
            return "exec_calls"
        if call_name in WEB_CALLS:
            return "web_calls"
        if call_name in FILE_CALLS:
            return "file_operations"
        leaf = call_name.rsplit(".", 1)[-1].lower()
        if leaf in MESSAGE_METHODS:
            return "message_sends"
        if any(leaf.startswith(prefix) for prefix in MESSAGE_PREFIXES):
            return "message_sends"
        return None


class _SourceScanner(ast.NodeVisitor):
    def __init__(
        self,
        source: str,
        file_path: Path,
        tool_key: str,
        classifier: _CallClassifier,
        max_examples_per_tool: int,
    ) -> None:
        self.source = source
        self.file_path = file_path
        self.tool_key = tool_key
        self.classifier = classifier
        self.max_examples_per_tool = max_examples_per_tool
        self.capabilities: Set[str] = set()
        self.examples: List[UsageExample] = []

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        call_name = self._call_name(node.func)
        capability = self.classifier.classify(call_name) if call_name else None
        if capability:
            self.capabilities.add(capability)
            snippet = ast.get_source_segment(self.source, node) or call_name
            example = UsageExample(
                file=self.tool_key,
                line=getattr(node, "lineno", 0),
                call=call_name,
                snippet=" ".join(snippet.strip().split()),
            )
            if len(self.examples) < self.max_examples_per_tool:
                self.examples.append(example)
        self.generic_visit(node)

    def _call_name(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            base = self._call_name(node.value)
            return f"{base}.{node.attr}" if base else node.attr
        return ""


class ToolDiscovery:
    def __init__(
        self,
        root_dir: Path | str = ".",
        scan_dirs: Sequence[str] = ("scripts", "src"),
        *,
        max_examples_per_tool: int = 5,
    ) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.scan_dirs = tuple(scan_dirs)
        self.max_examples_per_tool = max_examples_per_tool
        self.classifier = _CallClassifier()

    def discover_tools(self) -> List[ToolRecord]:
        tools: Dict[str, ToolRecord] = {}
        for path in self._iter_python_files():
            tool_key = path.relative_to(self.root_dir).as_posix()
            module_path = self._module_path(path)
            record = ToolRecord(name=path.stem, module_path=module_path)
            source = path.read_text(encoding="utf-8", errors="ignore")
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            scanner = _SourceScanner(
                source=source,
                file_path=path,
                tool_key=tool_key,
                classifier=self.classifier,
                max_examples_per_tool=self.max_examples_per_tool,
            )
            scanner.visit(tree)
            if scanner.capabilities:
                record.capabilities.update(scanner.capabilities)
                for example in scanner.examples:
                    record.add_example(example, self.max_examples_per_tool)
                tools[tool_key] = record
        return sorted(tools.values(), key=lambda item: item.module_path)

    def write_inventory(self, output_path: Path | str, tools: Sequence[ToolRecord]) -> Path:
        output = Path(output_path)
        if not output.is_absolute():
            output = self.root_dir / output
        payload = {
            "generated_from": [str((self.root_dir / directory).resolve()) for directory in self.scan_dirs],
            "tool_count": len(tools),
            "tools": [tool.to_inventory_entry() for tool in tools],
        }
        output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return output

    def write_markdown(self, output_path: Path | str, tools: Sequence[ToolRecord]) -> Path:
        output = Path(output_path)
        if not output.is_absolute():
            output = self.root_dir / output
        lines: List[str] = [
            "# Tool Inventory",
            "",
            "Auto-discovered tools in `scripts/` and `src/` based on call-pattern scanning.",
            "",
            f"- Total discovered tools: **{len(tools)}**",
            "",
        ]
        if not tools:
            lines.append("No tools matching discovery patterns were found.")
        for tool in tools:
            lines.extend(
                [
                    f"## {tool.name}",
                    "",
                    f"- Module path: `{tool.module_path}`",
                    f"- Capabilities: {', '.join(f'`{cap}`' for cap in sorted(tool.capabilities))}",
                    "- Usage examples:",
                ]
            )
            for example in tool.usage_examples:
                lines.append(
                    f"  - `{example.file}:{example.line}` `{example.call}` - `{example.snippet}`"
                )
            if not tool.usage_examples:
                lines.append("  - _(none captured)_")
            lines.append("")
        output.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return output

    def _iter_python_files(self) -> Iterable[Path]:
        for directory in self.scan_dirs:
            base = self.root_dir / directory
            if not base.exists():
                continue
            for path in sorted(base.rglob("*.py")):
                if path.is_file():
                    yield path

    def _module_path(self, path: Path) -> str:
        relative = path.relative_to(self.root_dir).with_suffix("")
        return ".".join(relative.parts)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Discover tool-like modules and generate docs.")
    parser.add_argument(
        "--root-dir",
        default=".",
        help="Repository root to scan.",
    )
    parser.add_argument(
        "--inventory-output",
        default="tools_inventory.json",
        help="Path for JSON inventory output.",
    )
    parser.add_argument(
        "--markdown-output",
        default="TOOLS.md",
        help="Path for Markdown inventory output.",
    )
    parser.add_argument(
        "--scan-dirs",
        nargs="+",
        default=["scripts", "src"],
        help="Directories to scan recursively.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    discovery = ToolDiscovery(root_dir=args.root_dir, scan_dirs=args.scan_dirs)
    tools = discovery.discover_tools()
    json_path = discovery.write_inventory(args.inventory_output, tools)
    markdown_path = discovery.write_markdown(args.markdown_output, tools)
    print(f"Discovered {len(tools)} tools.")
    print(f"Wrote inventory: {json_path}")
    print(f"Wrote docs: {markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
