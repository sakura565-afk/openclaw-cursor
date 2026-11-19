"""Traffic source dimensions and human-readable labels for Metrika reports."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

UNDEFINED_SOURCE_LABEL = "Не определён"

_SEARCH_ENGINE_BRACKET = re.compile(r"^ym:s:searchEngine<([^>]+)>$", re.IGNORECASE)
_SEARCH_ENGINE_SPACE = re.compile(r"^ym:s:searchEngine\s+(\S+)\s*$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class SourceDimension:
    dimension: str
    name_ru: str
    show_in_report: bool


SOURCE_DIMENSIONS: tuple[SourceDimension, ...] = (
    SourceDimension("ym:s:source", "Источник", True),
    SourceDimension("ym:s:UTMSource", "UTM Source", True),
    SourceDimension("ym:s:UTMCampaign", "UTM Campaign", True),
    SourceDimension("ym:s:referrer", "Реферер", True),
    SourceDimension("ym:s:searchEngine", "Поисковая система", True),
    SourceDimension("ym:s:direct", "Прямые заходы", True),
    SourceDimension("ym:s:organic", "Органический поиск", True),
    SourceDimension("ym:s:social", "Социальные сети", True),
    SourceDimension("ym:s:email", "Email", True),
    SourceDimension("ym:s:ads", "Реклама", True),
    SourceDimension("ym:s:campaign", "Кампания", False),
)


def dimensions_for_report(dimensions: Sequence[SourceDimension] | None = None) -> list[SourceDimension]:
    """Dimensions marked visible in reports."""
    source = dimensions if dimensions is not None else SOURCE_DIMENSIONS
    return [d for d in source if d.show_in_report]


def _normalize_raw(raw: str | None) -> str:
    if raw is None:
        return ""
    return " ".join(str(raw).split())


def _title_preserve_cyrillic(token: str) -> str:
    token = token.strip()
    if not token:
        return token
    if token.isupper() or token.islower():
        return token
    return token[0].upper() + token[1:]


def parse_source(raw: str | None) -> str:
    """Turn a Metrika source dimension value into a report label."""
    text = _normalize_raw(raw)
    if not text:
        return UNDEFINED_SOURCE_LABEL

    folded = text.casefold()
    if folded == "ym:s:direct":
        return "Прямые заходы"
    if folded == "ym:s:organic":
        return "Органический поиск"

    bracket = _SEARCH_ENGINE_BRACKET.match(text)
    if bracket:
        return _title_preserve_cyrillic(bracket.group(1))

    spaced = _SEARCH_ENGINE_SPACE.match(text)
    if spaced:
        return _title_preserve_cyrillic(spaced.group(1))

    if folded.startswith("ym:s:"):
        rest = text[5:]
        if ":" in rest:
            source_part, campaign_part = rest.split(":", 1)
            source_part = source_part.strip()
            campaign_part = campaign_part.strip()
            if source_part and campaign_part and source_part.casefold() not in (
                "direct",
                "organic",
                "searchengine",
            ):
                return f"{source_part} / {campaign_part}"
        if rest and ":" not in rest and rest.casefold() not in ("direct", "organic"):
            if not rest.casefold().startswith("searchengine"):
                return rest

    return text