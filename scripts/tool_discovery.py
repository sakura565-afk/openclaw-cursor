#!/usr/bin/env python3
"""Discover OpenClaw tools from npm dist output and skill directories."""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _repo_root(explicit: Path | None = None) -> Path:
    if explicit is not None:
        return explicit.resolve()
    env = os.environ.get("TOOL_DISCOVERY_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    here = Path(__file__).resolve()
    if here.parent.name == "scripts":
        return here.parent.parent
    return here.parent


def _skills_scan_roots(repo: Path) -> list[Path]:
    roots: list[Path] = []
    for rel in ("skills", "src/skills", ".cursor/skills"):
        p = (repo / rel).resolve()
        if p.is_dir():
            roots.append(p)
    home = Path.home()
    for rel in (home / ".openclaw" / "skills", home / ".openclaw" / "workspace" / "skills"):
        if rel.is_dir():
            roots.append(rel.resolve())
    seen: set[str] = set()
    out: list[Path] = []
    for r in roots:
        key = str(r)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _openclaw_dist(repo: Path) -> Path | None:
    dist = repo / "node_modules" / "openclaw" / "dist"
    return dist if dist.is_dir() else None


def _learnings_tools(repo: Path) -> Path:
    return repo / ".learnings" / "tools"


def _last_scan_path(repo: Path) -> Path:
    return _learnings_tools(repo) / "_last_scan.json"


def _slug(name: str, source_hint: str = "") -> str:
    base = re.sub(r"[^a-zA-Z0-9._-]+", "-", name.strip()).strip("-_.")[:72]
    if not base:
        base = "tool"
    if source_hint:
        h = hex(abs(hash(source_hint)))[2:10]
        return f"{base}-{h}"
    return base


@dataclass
class DiscoveredTool:
    """Unified record for a discovered tool."""

    name: str
    description: str
    parameters: dict[str, Any]
    source_file: str
    source_kind: str
    extra: dict[str, Any] = field(default_factory=dict)

    def tool_id(self) -> str:
        return f"{self.source_kind}:{self.source_file}:{self.name}"

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "source_file": self.source_file,
            "source_kind": self.source_kind,
            "tool_id": self.tool_id(),
            "extra": self.extra,
        }


def _parse_simple_yaml_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    text = raw.lstrip("\ufeff")
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    if len(lines) < 2 or lines[0].strip() != "---":
        return {}, text
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}, text
    fm_block = "\n".join(lines[1:end])
    body = "\n".join(lines[end + 1 :]).lstrip("\n")
    meta: dict[str, Any] = {}
    for line in fm_block.splitlines():
        if ":" not in line or line.strip().startswith("#"):
            continue
        key, _, rest = line.partition(":")
        key = key.strip()
        val = rest.strip().strip('"').strip("'")
        if key:
            meta[key] = val
    return meta, body


def _first_paragraph(body: str) -> str:
    paras = [p.strip() for p in re.split(r"\n{2,}", body) if p.strip()]
    if not paras:
        return ""
    return paras[0].replace("\n", " ").strip()


def discover_from_skill_md(path: Path, repo: Path) -> DiscoveredTool | None:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    meta, body = _parse_simple_yaml_frontmatter(raw)
    name = str(meta.get("name") or meta.get("title") or path.parent.name).strip()
    if not name:
        return None
    desc = str(meta.get("description", "")).strip() or _first_paragraph(body) or "No description."
    params_raw = meta.get("parameters") or meta.get("input_schema") or meta.get("schema")
    parameters: dict[str, Any] = {}
    if isinstance(params_raw, str) and params_raw.strip().startswith("{"):
        try:
            parameters = json.loads(params_raw)
        except json.JSONDecodeError:
            parameters = {"_raw": params_raw}
    rel = _relative_to_repo(path, repo)
    return DiscoveredTool(
        name=name,
        description=desc,
        parameters=parameters,
        source_file=rel.as_posix(),
        source_kind="skill_markdown",
        extra={"skill_id": meta.get("id", "")},
    )


def _relative_to_repo(path: Path, repo: Path) -> Path:
    try:
        return path.resolve().relative_to(repo.resolve())
    except ValueError:
        return path.resolve()


def _literal_str(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _dict_from_ast(node: ast.AST) -> dict[str, Any] | None:
    if not isinstance(node, ast.Dict):
        return None
    out: dict[str, Any] = {}
    for k, v in zip(node.keys, node.values, strict=False):
        if k is None:
            continue
        key = _literal_str(k) if isinstance(k, ast.Constant) else None
        if not key:
            if isinstance(k, ast.Str):  # pragma: no cover - py<3.8 compat unused
                key = k.s
            else:
                continue
        if isinstance(v, ast.Dict):
            nested = _dict_from_ast(v)
            if nested is not None:
                out[key] = nested
        elif isinstance(v, ast.List):
            items: list[Any] = []
            for el in v.elts:
                if isinstance(el, ast.Dict):
                    nd = _dict_from_ast(el)
                    if nd is not None:
                        items.append(nd)
                else:
                    s = _literal_str(el)
                    if s is not None:
                        items.append(s)
            out[key] = items
        else:
            s = _literal_str(v)
            if s is not None:
                out[key] = s
            elif isinstance(v, ast.Constant) and isinstance(v.value, (int, float, bool)):
                out[key] = v.value
    return out


def discover_from_python_skill(path: Path, repo: Path) -> list[DiscoveredTool]:
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(path))
    except (OSError, SyntaxError):
        return []
    rel = _relative_to_repo(path, repo)
    found: list[DiscoveredTool] = []

    class V(ast.NodeVisitor):
        def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in (
                    "TOOL",
                    "TOOL_SPEC",
                    "TOOL_DEFINITION",
                    "OPENCLAW_TOOL",
                ):
                    d = _dict_from_ast(node.value)
                    if d and "name" in d:
                        self._emit_from_dict(d)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
            if isinstance(node.target, ast.Name) and node.target.id in (
                "TOOL",
                "TOOL_SPEC",
            ):
                if node.value:
                    d = _dict_from_ast(node.value)
                    if d and "name" in d:
                        self._emit_from_dict(d)

        def _emit_from_dict(self, d: dict[str, Any]) -> None:
            name = str(d.get("name", "")).strip()
            if not name:
                return
            desc = str(d.get("description", d.get("summary", ""))).strip() or "No description."
            params = d.get("parameters") or d.get("input_schema") or {}
            if not isinstance(params, dict):
                params = {"value": params}
            found.append(
                DiscoveredTool(
                    name=name,
                    description=desc,
                    parameters=params,
                    source_file=rel.as_posix(),
                    source_kind="skill_python",
                )
            )

    V().visit(tree)

    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            d = _dict_from_ast(node)
            if not d:
                continue
            if "name" in d and "description" in d and ("parameters" in d or "input_schema" in d):
                name = str(d.get("name", "")).strip()
                if not name:
                    continue
                params = d.get("parameters") or d.get("input_schema") or {}
                if not isinstance(params, dict):
                    params = {}
                found.append(
                    DiscoveredTool(
                        name=name,
                        description=str(d.get("description", "")).strip() or "No description.",
                        parameters=params,
                        source_file=rel.as_posix(),
                        source_kind="skill_python_literal",
                    )
                )
    dedup: dict[str, DiscoveredTool] = {}
    for t in found:
        dedup[t.tool_id()] = t
    return list(dedup.values())


def _balanced_json_object(s: str, start: int) -> tuple[str, int] | None:
    if start >= len(s) or s[start] != "{":
        return None
    depth = 0
    in_str: str | None = None
    escape = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == in_str:
                in_str = None
            continue
        if ch in "\"'":
            in_str = ch
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start : i + 1], i + 1
    return None


def _js_string_literal(text: str, key_pos: int, key: str) -> str | None:
    m = re.search(rf"{re.escape(key)}\s*:\s*", text[key_pos:], re.DOTALL)
    if not m:
        return None
    pos = key_pos + m.end()
    while pos < len(text) and text[pos] in " \t\n\r":
        pos += 1
    if pos >= len(text):
        return None
    quote = text[pos]
    if quote not in "\"'":
        return None
    pos += 1
    buf: list[str] = []
    esc = False
    while pos < len(text):
        c = text[pos]
        if esc:
            buf.append(c)
            esc = False
        elif c == "\\":
            esc = True
        elif c == quote:
            return "".join(buf)
        else:
            buf.append(c)
        pos += 1
    return None


def discover_from_js_file(path: Path, repo: Path) -> list[DiscoveredTool]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    rel = _relative_to_repo(path, repo)
    tools: list[DiscoveredTool] = []

    for m in re.finditer(r"type\s*:\s*['\"]function['\"]", text):
        window_start = max(0, m.start() - 4000)
        window = text[window_start : m.start() + 4000]
        local_off = m.start() - window_start
        name = _js_string_literal(window, local_off, "name")
        if not name:
            fn_m = re.search(r"function\s*:\s*\{", window[local_off : local_off + 500])
            if fn_m:
                name = _js_string_literal(window, local_off + fn_m.start(), "name")
        desc = _js_string_literal(window, local_off, "description") or ""
        params: dict[str, Any] = {}
        pm = re.search(r"parameters\s*:\s*", window[local_off :])
        if pm:
            abs_start = local_off + pm.end()
            sub = window[abs_start:]
            brace_at = None
            for i, ch in enumerate(sub):
                if ch == "{":
                    brace_at = abs_start + i
                    break
            if brace_at is not None:
                blob = _balanced_json_object(window, brace_at)
                if blob:
                    raw_obj, _ = blob
                    try:
                        params = json.loads(raw_obj)
                    except json.JSONDecodeError:
                        params = {"_unparsed": raw_obj[:2000]}

        if name:
            tools.append(
                DiscoveredTool(
                    name=name,
                    description=desc or "No description.",
                    parameters=params if isinstance(params, dict) else {},
                    source_file=rel.as_posix(),
                    source_kind="openclaw_dist",
                )
            )

    seen_names: set[str] = set()
    deduped: list[DiscoveredTool] = []
    for t in tools:
        key = f"{t.name}@{t.source_file}"
        if key in seen_names:
            continue
        seen_names.add(key)
        deduped.append(t)
    return deduped


def scan_all(repo: Path) -> list[DiscoveredTool]:
    all_tools: list[DiscoveredTool] = []
    dist = _openclaw_dist(repo)
    if dist:
        for js in sorted(dist.rglob("*.js")):
            all_tools.extend(discover_from_js_file(js, repo))
        for ext in ("*.mjs", "*.cjs"):
            for js in sorted(dist.rglob(ext)):
                all_tools.extend(discover_from_js_file(js, repo))

    for root in _skills_scan_roots(repo):
        for md in sorted(root.rglob("SKILL.md")):
            if "node_modules" in md.parts:
                continue
            t = discover_from_skill_md(md, repo)
            if t:
                all_tools.append(t)
        for py in sorted(root.rglob("*.py")):
            if py.name == "__init__.py" or "node_modules" in py.parts:
                continue
            all_tools.extend(discover_from_python_skill(py, repo))

    by_id: dict[str, DiscoveredTool] = {}
    for t in all_tools:
        by_id[t.tool_id()] = t
    return list(by_id.values())


def _write_tool_json(repo: Path, tool: DiscoveredTool) -> Path:
    out_dir = _learnings_tools(repo)
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = _slug(tool.name, tool.source_file) + ".json"
    path = out_dir / fname
    path.write_text(json.dumps(tool.to_json_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _load_previous_tool_ids(repo: Path) -> set[str]:
    p = _last_scan_path(repo)
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    ids = data.get("tool_ids")
    if isinstance(ids, list):
        return {str(x) for x in ids}
    return set()


def _write_last_scan(repo: Path, tools: list[DiscoveredTool]) -> None:
    payload = {
        "scan_time": datetime.now(timezone.utc).isoformat(),
        "tool_ids": sorted({t.tool_id() for t in tools}),
        "tool_names": sorted({t.name for t in tools}),
    }
    out = _last_scan_path(repo)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _notify_new_tools(repo: Path, tools: list[DiscoveredTool], verbose: bool) -> list[DiscoveredTool]:
    previous = _load_previous_tool_ids(repo)
    current_ids = {t.tool_id() for t in tools}
    new_tools = [t for t in tools if t.tool_id() not in previous]

    if not previous:
        msg = f"[tool_discovery] First scan: indexed {len(tools)} tool(s). Subsequent scans will report newly added tools."
        print(msg, file=sys.stderr)
        return new_tools

    if new_tools:
        names = ", ".join(sorted(t.name for t in new_tools))
        print(
            f"[tool_discovery] NOTICE: {len(new_tools)} new tool(s) since last scan: {names}",
            file=sys.stderr,
        )
        if verbose:
            for t in sorted(new_tools, key=lambda x: x.name.lower()):
                print(f"  - {t.name} ({t.source_kind}) {t.source_file}", file=sys.stderr)
    elif verbose:
        print("[tool_discovery] No new tools since last scan.", file=sys.stderr)

    return new_tools


def generate_tools_md(repo: Path, tools: list[DiscoveredTool]) -> Path:
    out = repo / "tools.md"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    by_kind: dict[str, int] = {}
    for t in tools:
        by_kind[t.source_kind] = by_kind.get(t.source_kind, 0) + 1

    lines = [
        "# OpenClaw tool catalog",
        "",
        f"_Generated {ts} by `scripts/tool_discovery.py`._",
        "",
        "## Summary",
        "",
        f"- **Total tools:** {len(tools)}",
    ]
    for kind, n in sorted(by_kind.items()):
        lines.append(f"- **{kind}:** {n}")
    lines.extend(["", "## Tools", "", "| Name | Description | Parameters | Source |", "| --- | --- | --- | --- |"])
    for t in sorted(tools, key=lambda x: (x.name.lower(), x.source_file)):
        desc = t.description.replace("|", "\\|")
        if len(desc) > 160:
            desc = desc[:157] + "..."
        pbrief = ""
        if t.parameters:
            pbrief = json.dumps(t.parameters, sort_keys=True)
            if len(pbrief) > 120:
                pbrief = pbrief[:117] + "..."
        src = t.source_file.replace("|", "\\|")
        lines.append(f"| `{t.name}` | {desc} | `{pbrief}` | `{src}` |")
    lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def cmd_scan(repo: Path, verbose: bool) -> int:
    if verbose:
        print(f"[tool_discovery] Repository root: {repo}", file=sys.stderr)
        dist = _openclaw_dist(repo)
        print(f"[tool_discovery] openclaw dist: {dist or '(missing)'}", file=sys.stderr)
        for r in _skills_scan_roots(repo):
            print(f"[tool_discovery] skills root: {r}", file=sys.stderr)

    tools = scan_all(repo)
    if verbose:
        print(f"[tool_discovery] Discovered {len(tools)} tool(s).", file=sys.stderr)

    learn = _learnings_tools(repo)
    learn.mkdir(parents=True, exist_ok=True)
    for old in learn.glob("*.json"):
        if old.name != "_last_scan.json":
            try:
                old.unlink()
            except OSError:
                pass

    for t in tools:
        _write_tool_json(repo, t)

    new_list = _notify_new_tools(repo, tools, verbose)
    _write_last_scan(repo, tools)

    md_path = generate_tools_md(repo, tools)
    print(json.dumps({"written": len(tools), "tools_md": md_path.as_posix(), "new_since_last": len(new_list)}, indent=2))
    return 0


def _load_stored_tools(repo: Path) -> list[dict[str, Any]]:
    learn = _learnings_tools(repo)
    if not learn.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for p in sorted(learn.glob("*.json")):
        if p.name == "_last_scan.json":
            continue
        try:
            items.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return items


def cmd_list(repo: Path, pattern: str | None, verbose: bool) -> int:
    rows = _load_stored_tools(repo)
    if not rows and verbose:
        print("[tool_discovery] No stored tools; run `scan` first.", file=sys.stderr)
    if pattern:
        rx = re.compile(pattern, re.IGNORECASE)
        rows = [r for r in rows if rx.search(r.get("name", "")) or rx.search(r.get("source_file", ""))]
    for r in sorted(rows, key=lambda x: str(x.get("name", "")).lower()):
        line = f"{r.get('name')}\t{r.get('source_kind')}\t{r.get('source_file')}"
        print(line)
    return 0


def cmd_show(repo: Path, name: str) -> int:
    rows = _load_stored_tools(repo)
    needle = name.strip().lower()
    matches = [
        r
        for r in rows
        if str(r.get("name", "")).lower() == needle
        or str(r.get("tool_id", "")).lower() == needle
        or needle in str(r.get("source_file", "")).lower()
    ]
    if len(matches) != 1:
        print(
            json.dumps(
                {
                    "error": "ambiguous_or_missing" if matches else "not_found",
                    "query": name,
                    "match_count": len(matches),
                },
                indent=2,
            )
        )
        return 2 if not matches else 3
    print(json.dumps(matches[0], indent=2, sort_keys=True))
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tool_discovery.py",
        description="Scan OpenClaw dist and skill trees; write .learnings/tools/*.json and tools.md.",
    )
    p.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repository root (default: parent of scripts/ or TOOL_DISCOVERY_ROOT).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("scan", help="Scan sources and refresh JSON catalog + tools.md")
    sp.add_argument("--verbose", "-v", action="store_true", help="Log scan paths and details.")

    lp = sub.add_parser("list", help="List tools from the last scan (JSON files under .learnings/tools/)")
    lp.add_argument("--filter", "-f", metavar="PATTERN", help="Regex filter on name or source_file.")
    lp.add_argument("--verbose", "-v", action="store_true")

    sh = sub.add_parser("show", help="Print one tool record as JSON")
    sh.add_argument("tool_name", help="Tool name, tool_id, or distinctive source path fragment.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    repo = _repo_root(getattr(args, "root", None))

    if args.command == "scan":
        return cmd_scan(repo, verbose=args.verbose)
    if args.command == "list":
        return cmd_list(repo, getattr(args, "filter", None), verbose=args.verbose)
    if args.command == "show":
        return cmd_show(repo, args.tool_name)
    return 1


if __name__ == "__main__":
    sys.exit(main())
