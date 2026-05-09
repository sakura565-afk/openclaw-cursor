"""Backward-compatible entry point; implementation lives in cursor-repo/scripts/."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_IMPL = Path(__file__).resolve().parent.parent / "cursor-repo" / "scripts" / "auto_reflection.py"
_spec = importlib.util.spec_from_file_location("_cursor_repo_auto_reflection", _IMPL)
if _spec is None or _spec.loader is None:
    raise RuntimeError(f"Cannot load auto_reflection implementation from {_IMPL}")

_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _module
_spec.loader.exec_module(_module)

for _attr in dir(_module):
    if _attr.startswith("_"):
        continue
    globals()[_attr] = getattr(_module, _attr)

del _spec, _module, _IMPL, _attr

if __name__ == "__main__":
    raise SystemExit(main())
