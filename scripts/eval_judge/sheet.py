"""The blind contact sheet.

The point of the sheet is that the deciding model must NOT know which variant
is which. So:

* the variants are shuffled with a fixed seed (``--seed``), so the run is
  reproducible but the order is not the argument order;
* every cell is labelled with a bare letter (A, B, C, ...) - no file name, no
  candidate name, no "reference";
* the sheet is built from the crops only, so nothing in the pixels identifies
  the source either.

The mapping letter -> variant is written to report.json, because the scores
have to be attributed back to the candidates afterwards. It is NOT written on
the image.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageDraw

# Cell geometry of the contact sheet.
CELL_SIZE = 160
MARGIN = 16
LABEL_WIDTH = 48
HEADER_HEIGHT = 28
BACKGROUND = (24, 24, 26)
FOREGROUND = (238, 238, 238)
GRID = (70, 70, 74)
FONT_SIZE = 18


def blind_labels(count: int) -> list[str]:
    """A, B, C, ... for ``count`` variants.

    Past Z the labels continue AA, AB, ... so the label can never be
    confused with a single letter.
    """
    labels: list[str] = []
    index = 0
    while len(labels) < count:
        index += 1
        labels.append(_spreadsheet_labels(index))
    return labels


def _spreadsheet_labels(index: int) -> str:
    """1 -> A, 26 -> Z, 27 -> AA."""
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters or "A"


def shuffle_variants(variants: Sequence[str], seed: int) -> list[str]:
    """Deterministic permutation of the variant names."""
    shuffled = list(variants)
    random.Random(seed).shuffle(shuffled)
    return shuffled


def build_sheet(
    rows: Sequence[Sequence[str]],
    labels: Sequence[str],
    out_path: Path,
    *,
    cell_size: int = CELL_SIZE,
    title: str = "",
) -> Path:
    """Write the contact sheet.

    ``rows`` is one row per variant (in blind order); each row is the list of
    crop paths of that variant, in the order the crops were numbered.
    ``labels`` are the blind letters, one per row.
    """
    columns = max((len(row) for row in rows), default=0)
    rows = list(rows)
    width = MARGIN * 2 + LABEL_WIDTH + columns * cell_size
    header = HEADER_HEIGHT + (18 if title else 0)
    height = MARGIN * 2 + header + max(1, len(rows)) * cell_size

    sheet = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(sheet)

    y = MARGIN
    if title:
        draw.text((MARGIN, y), title, fill=FOREGROUND)
        y += 18

    for row_index, (row, label) in enumerate(zip(rows, labels)):
        top = y + row_index * cell_size
        draw.text((MARGIN, top + cell_size // 2 - FONT_SIZE // 2), label, fill=FOREGROUND)
        for column_index, path in enumerate(row):
            left = MARGIN + LABEL_WIDTH + column_index * cell_size
            try:
                with Image.open(path) as image:
                    cell = image.convert("RGB").resize((cell_size, cell_size), Image.LANCZOS)
            except (OSError, ValueError):
                cell = Image.new("RGB", (cell_size, cell_size), (60, 20, 20))
            sheet.paste(cell, (left, top))
            draw.rectangle(
                [left, top, left + cell_size - 1, top + cell_size - 1], outline=GRID, width=1
            )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path)
    return out_path
