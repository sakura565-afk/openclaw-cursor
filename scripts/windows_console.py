"""Windows console output helpers.

Windows native tools (``schtasks``, ``ipconfig``, ``net``, ...) write to the
console in the OEM code page of the active language, NOT in UTF-8. On a
Russian Windows that is cp866; on a Western one it is cp437. Reading such
output with ``subprocess.run(..., text=True)`` decodes it as UTF-8 and raises
``UnicodeDecodeError`` — the tool then appears to fail for no reason.

Use :func:`decode_console_output` on the raw bytes instead.
"""

from __future__ import annotations

from typing import Union

# Tried in order. cp866 first because that is what a Russian Windows uses.
CONSOLE_ENCODINGS = ("cp866", "cp1251", "cp437", "utf-8")


def decode_console_output(raw: Union[bytes, str, None]) -> str:
    """Decode native Windows console output, trying the known code pages."""
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    for encoding in CONSOLE_ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode(CONSOLE_ENCODINGS[0], errors="replace")
