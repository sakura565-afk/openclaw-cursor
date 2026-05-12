#!/usr/bin/env python3
"""Shim: full implementation lives in repository-root ``tool_discovery.py``."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_MOD_NAME = "_openclaw_tool_discovery_root"
_SPEC = importlib.util.spec_from_file_location(_MOD_NAME, _ROOT / "tool_discovery.py")
assert _SPEC and _SPEC.loader
_MOD = importlib.util.module_from_spec(_SPEC)
sys.modules[_MOD_NAME] = _MOD
_SPEC.loader.exec_module(_MOD)

analyze_scripts = _MOD.analyze_scripts
ToolProfile = _MOD.ToolProfile
generate_markdown = _MOD.generate_markdown
suggest_tools = _MOD.suggest_tools
discover_script_paths = _MOD.discover_script_paths
main_script_cli = _MOD.main_script_cli


def main(argv: list[str] | None = None) -> int:
    return main_script_cli(argv)


if __name__ == "__main__":
    import sys

    sys.exit(main())
