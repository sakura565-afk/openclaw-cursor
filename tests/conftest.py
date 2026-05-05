from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--self-test",
        action="store_true",
        default=False,
        help="Run tests marked as self_test.",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "self_test: script module self-test coverage")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--self-test"):
        skip_non_self = pytest.mark.skip(reason="requires --self-test option")
        for item in items:
            if "self_test" not in item.keywords:
                item.add_marker(skip_non_self)
