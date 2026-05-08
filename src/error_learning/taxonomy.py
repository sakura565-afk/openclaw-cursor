"""Heuristic error categorization from unstructured log or traceback text."""

from __future__ import annotations

import re
from enum import Enum
from typing import NamedTuple


class ErrorCategory(str, Enum):
    SYNTAX = "syntax"
    IMPORT = "import"
    AUTH = "auth"
    NETWORK = "network"
    TIMEOUT = "timeout"
    RESOURCE = "resource"
    CONFIG = "config"
    IO = "io"
    RUNTIME = "runtime"
    UNKNOWN = "unknown"


class Classification(NamedTuple):
    category: ErrorCategory
    signals: tuple[str, ...]


_RULES: list[tuple[ErrorCategory, tuple[re.Pattern[str], ...]]] = [
    (
        ErrorCategory.SYNTAX,
        (
            re.compile(r"\bsyntaxerror\b", re.I),
            re.compile(r"\binvalid syntax\b", re.I),
            re.compile(r"\bunexpected indent\b", re.I),
            re.compile(r"\bunindent\b", re.I),
        ),
    ),
    (
        ErrorCategory.IMPORT,
        (
            re.compile(r"\bmodulenotfounderror\b", re.I),
            re.compile(r"\bimporterror\b", re.I),
            re.compile(r"\bno module named\b", re.I),
            re.compile(r"\bcannot import name\b", re.I),
        ),
    ),
    (
        ErrorCategory.AUTH,
        (
            re.compile(r"\b401\b|\b403\b", re.I),
            re.compile(r"\bunauthorized\b|\bforbidden\b", re.I),
            re.compile(r"\bpermission denied\b", re.I),
            re.compile(r"\bauthentication failed\b", re.I),
            re.compile(r"\binvalid (api )?key\b", re.I),
        ),
    ),
    (
        ErrorCategory.NETWORK,
        (
            re.compile(r"\bconnection (refused|reset|aborted)\b", re.I),
            re.compile(r"\beconnrefused\b|\beconnreset\b|\betimedout\b", re.I),
            re.compile(r"\bgetaddrinfo\b|\bname or service not known\b", re.I),
            re.compile(r"\bssl\b.*\berror\b|\bcertificate\b", re.I),
            re.compile(r"\burllib\.error\b|\bhttp error\b", re.I),
            re.compile(r"\b50[0-9]\b", re.I),
        ),
    ),
    (
        ErrorCategory.TIMEOUT,
        (
            re.compile(r"\btimed out\b|\btimeout\b", re.I),
            re.compile(r"\bdeadline exceeded\b", re.I),
        ),
    ),
    (
        ErrorCategory.RESOURCE,
        (
            re.compile(r"\bno space left\b|\bdisk full\b", re.I),
            re.compile(r"\bout of memory\b|\boom\b|\bcannot allocate\b", re.I),
            re.compile(r"\bgpu\b.*\b(full|memory)\b", re.I),
        ),
    ),
    (
        ErrorCategory.CONFIG,
        (
            re.compile(r"\bmissing (environment|env|config)\b", re.I),
            re.compile(r"\binvalid (config|configuration)\b", re.I),
            re.compile(r"\bundefined (variable|setting)\b", re.I),
        ),
    ),
    (
        ErrorCategory.IO,
        (
            re.compile(r"\bfilenotfounderror\b", re.I),
            re.compile(r"\bno such file\b", re.I),
            re.compile(r"\bis a directory\b|\bnot a directory\b", re.I),
        ),
    ),
    (
        ErrorCategory.RUNTIME,
        (
            re.compile(
                r"\b(value|type|key|attribute|index|zerodivision|recursion)error\b",
                re.I,
            ),
            re.compile(r"\bruntimeerror\b|\bassertionerror\b|\bstopiteration\b", re.I),
        ),
    ),
]


def classify_error_text(text: str) -> Classification:
    """Assign a coarse category using ordered rules (first strong match wins)."""

    for category, patterns in _RULES:
        if any(p.search(text) for p in patterns):
            return Classification(category, signals=(category.value,))

    lowered = text.lower()
    if any(
        token in lowered
        for token in ("traceback", "exception", " error ", "failed", "failure")
    ):
        return Classification(ErrorCategory.UNKNOWN, signals=("unclassified_signal",))

    return Classification(ErrorCategory.UNKNOWN, signals=())
