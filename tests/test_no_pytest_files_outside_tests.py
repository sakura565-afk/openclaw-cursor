"""Guard: pytest only collects tests under tests/ (see pyproject.toml testpaths)."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Repository-relative paths allowed to look like tests but live outside tests/.
# Each entry must document why it is not collected by pytest.
ALLOWLIST: frozenset[str] = frozenset()

_SKIP_DIR_NAMES = {
    ".git",
    ".ci_comfy_stub",
    ".ci_scratch",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".venv",
    "venv",
    "node_modules",
}


def _looks_like_pytest_module(path: Path) -> bool:
    name = path.name
    return (name.startswith("test_") and name.endswith(".py")) or name.endswith(
        "_test.py"
    )


def find_pytest_filename_leaks(*, root: Path = REPO_ROOT) -> list[str]:
    """Return repo-relative paths that pytest would miss if placed outside tests/."""
    leaks: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if any(part in _SKIP_DIR_NAMES for part in path.parts):
            continue
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if relative.startswith("tests/"):
            continue
        if relative in ALLOWLIST:
            continue
        if _looks_like_pytest_module(path):
            leaks.append(relative)
    return leaks


def test_no_test_named_python_modules_outside_tests_directory() -> None:
    leaks = find_pytest_filename_leaks()
    assert not leaks, (
        "Files named test_*.py or *_test.py outside tests/ are not collected "
        '(pyproject.toml testpaths = ["tests"]) and look like missing CI coverage. '
        "Rename to measure_* / bench_* or move into tests/.\n"
        + "\n".join(f"  - {item}" for item in leaks)
    )


def test_guard_detects_artificial_scripts_test_leak(tmp_path: Path) -> None:
    """Regression: a new scripts/test_leak.py must fail the repository guard."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    leak = scripts / "test_leak.py"
    leak.write_text("def test_should_not_live_here():\n    assert False\n", encoding="utf-8")

    leaks = find_pytest_filename_leaks(root=tmp_path)
    assert leaks == ["scripts/test_leak.py"]
