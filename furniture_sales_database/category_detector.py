"""Category auto-detection for furniture product names."""

from __future__ import annotations

from typing import Dict, Iterable


CATEGORY_KEYWORDS: Dict[str, Iterable[str]] = {
    "Классика": (
        "классика",
        "classic",
        "барокко",
        "ампир",
        "резной",
        "патина",
    ),
    "Корпусная": (
        "шкаф",
        "тумба",
        "комод",
        "стеллаж",
        "полка",
        "корпус",
        "гардероб",
    ),
    "Мягкая": (
        "диван",
        "кресло",
        "пуф",
        "банкетка",
        "софа",
        "мягк",
    ),
    "Кухни": (
        "кухня",
        "кухон",
        "фасад",
        "остров",
        "пенал",
    ),
    "Офисная": (
        "офис",
        "рабочий стол",
        "ресепшн",
        "конференц",
        "переговорн",
    ),
    "Спальни": (
        "кровать",
        "спальня",
        "матрас",
        "прикроват",
        "туалетный стол",
    ),
}


def detect_category(product_name: str) -> str:
    """Detect a furniture category from a product name.

    Falls back to "Прочее" when no keywords are matched.
    """
    normalized = (product_name or "").strip().lower()
    if not normalized:
        return "Прочее"

    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(keyword in normalized for keyword in keywords):
            return category
    return "Прочее"
