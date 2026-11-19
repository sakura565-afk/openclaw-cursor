"""Load and validate the Metrika counter inventory (single source of truth)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_COUNTERS_PATH = _PACKAGE_DIR.parent / "docs" / "metrika_counters.json"

_REQUIRED_FIELDS = ("id", "name", "type", "domain")
_VALID_TYPES = frozenset({"main", "landing"})


@dataclass(frozen=True, slots=True)
class Counter:
    id: int
    name: str
    type: str
    domain: str


class CounterRegistryError(ValueError):
    """Invalid counter inventory."""


def _parse_counter(raw: dict[str, Any], index: int) -> Counter:
    missing = [field for field in _REQUIRED_FIELDS if field not in raw]
    if missing:
        raise CounterRegistryError(
            f"counter at index {index}: missing required field(s): {', '.join(missing)}"
        )

    counter_id = raw["id"]
    if isinstance(counter_id, bool) or not isinstance(counter_id, int):
        raise CounterRegistryError(
            f"counter at index {index}: id must be a positive integer, got {counter_id!r}"
        )
    if counter_id <= 0:
        raise CounterRegistryError(f"counter at index {index}: id must be positive, got {counter_id}")

    counter_type = raw["type"]
    if counter_type not in _VALID_TYPES:
        raise CounterRegistryError(
            f"counter at index {index}: type must be one of {sorted(_VALID_TYPES)}, got {counter_type!r}"
        )

    name = str(raw["name"]).strip()
    domain = str(raw["domain"]).strip()
    if not name:
        raise CounterRegistryError(f"counter at index {index}: name must not be empty")
    if not domain:
        raise CounterRegistryError(f"counter at index {index}: domain must not be empty")

    return Counter(id=counter_id, name=name, type=counter_type, domain=domain)


def validate_counters_payload(data: Any) -> list[Counter]:
    """Validate decoded JSON and return normalized counters."""
    if not isinstance(data, dict):
        raise CounterRegistryError("inventory root must be a JSON object")
    rows = data.get("counters")
    if not isinstance(rows, list):
        raise CounterRegistryError("'counters' must be a JSON array")

    counters: list[Counter] = []
    seen_ids: set[int] = set()
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise CounterRegistryError(f"counter at index {index}: must be an object")
        counter = _parse_counter(raw, index)
        if counter.id in seen_ids:
            raise CounterRegistryError(f"duplicate counter id: {counter.id}")
        seen_ids.add(counter.id)
        counters.append(counter)
    return counters


def load_counters(path: Path | None = None) -> list[Counter]:
    """Read counters from JSON; default path is ``docs/metrika_counters.json``."""
    inventory_path = path or DEFAULT_COUNTERS_PATH
    try:
        with inventory_path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as exc:
        raise CounterRegistryError(f"failed to read {inventory_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CounterRegistryError(f"invalid JSON in {inventory_path}: {exc}") from exc
    return validate_counters_payload(data)


def counters_by_type(counters: list[Counter]) -> dict[str, list[Counter]]:
    """Group counters by ``type`` (main / landing)."""
    grouped: dict[str, list[Counter]] = {"main": [], "landing": []}
    for counter in counters:
        grouped.setdefault(counter.type, []).append(counter)
    return grouped


def counters_report_order(counters: list[Counter]) -> list[Counter]:
    """Order for reports: main sites first, then landings, each group sorted by name."""
    mains = sorted((c for c in counters if c.type == "main"), key=lambda c: c.name.casefold())
    landings = sorted((c for c in counters if c.type == "landing"), key=lambda c: c.name.casefold())
    return mains + landings


def counter_id_label_pairs(counters: list[Counter] | None = None) -> list[tuple[int, str]]:
    """(id, display name) tuples in report order."""
    items = counters if counters is not None else load_counters()
    return [(c.id, c.name) for c in counters_report_order(items)]