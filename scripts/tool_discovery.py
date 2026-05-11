"""Compatibility wrapper; implementation lives in ``tool_discovery`` at repo root."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from tool_discovery import (  # noqa: E402
    SkillProfile,
    ToolProfile,
    analyze_module_tools,
    analyze_scripts,
    build_index_payload,
    default_catalog_dir,
    discover_all_tools,
    discover_skill_paths,
    format_list_text,
    generate_markdown,
    load_usage_stats,
    main,
    parse_skill_markdown,
    record_tool_usage,
    scan_roots,
    suggest_relevant_tools,
    suggest_tools,
    write_index,
)

__all__ = [
    "SkillProfile",
    "ToolProfile",
    "analyze_module_tools",
    "analyze_scripts",
    "build_index_payload",
    "default_catalog_dir",
    "discover_all_tools",
    "discover_skill_paths",
    "format_list_text",
    "generate_markdown",
    "load_usage_stats",
    "main",
    "parse_skill_markdown",
    "record_tool_usage",
    "scan_roots",
    "suggest_relevant_tools",
    "suggest_tools",
    "write_index",
]


def __getattr__(name: str):
    import tool_discovery as _impl

    return getattr(_impl, name)


if __name__ == "__main__":
    sys.exit(main())
