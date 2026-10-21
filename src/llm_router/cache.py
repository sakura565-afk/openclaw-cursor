"""LRU-кеш ответов модели с TTL и опциональной персистентностью.

Ключ — ``sha256(prompt + model + effort + image_hash)``. ``image_hash``
обязателен для vision-вызовов: без него запрос с другой картинкой и тем же
промптом вытащил бы из кеша чужой ответ.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Union

CACHE_VERSION = 1


def hash_image(image: Union[bytes, bytearray, memoryview, str, None]) -> str:
    """sha256 картинки (байты или путь/строка). ``None`` -> пустая строка."""

    if image is None:
        return ""
    if isinstance(image, (bytes, bytearray, memoryview)):
        return hashlib.sha256(bytes(image)).hexdigest()
    return hashlib.sha256(str(image).encode("utf-8")).hexdigest()


def make_cache_key(
    prompt: str,
    model: str,
    effort: Optional[str] = None,
    image: Union[bytes, bytearray, memoryview, str, None] = None,
    *,
    image_hash: Optional[str] = None,
) -> str:
    """Собрать ключ кеша. ``image_hash`` можно передать уже посчитанным."""

    digest = hashlib.sha256()
    resolved_image = image_hash if image_hash is not None else hash_image(image)
    for part in (prompt, model, effort or "", resolved_image):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x1f")  # разделитель, чтобы склейка полей не совпала случайно
    return digest.hexdigest()


def atomic_write_json(path: Path, payload: Any) -> None:
    """Атомарная запись JSON: сначала в temp рядом, потом ``os.replace``."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent), prefix=path.name, suffix=".tmp", delete=False
    )
    try:
        with handle as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(handle.name, path)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise


class LRUTTLCache:
    """Потокобезопасный LRU + TTL.

    ``clock`` подменяется в тестах, чтобы истечение TTL не требовало ожидания.
    """

    def __init__(
        self,
        max_size: int = 256,
        ttl_sec: float = 3600.0,
        *,
        path: Optional[Path] = None,
        persist: bool = False,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if max_size < 1:
            raise ValueError("max_size должен быть >= 1")
        self.max_size = int(max_size)
        self.ttl_sec = float(ttl_sec)
        self.path = Path(path) if path else None
        self.persist = bool(persist and self.path is not None)
        self._clock = clock
        self._lock = threading.RLock()
        self._entries: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        if self.persist:
            self.load()

    # --- основной API ---

    def get(self, key: str) -> Optional[Any]:
        """Значение по ключу или ``None``. Истёкшая запись считается промахом."""

        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._misses += 1
                return None
            if self._is_expired(entry):
                del self._entries[key]
                self._evictions += 1
                self._misses += 1
                return None
            self._entries.move_to_end(key)
            self._hits += 1
            return entry["value"]

    def set(self, key: str, value: Any) -> None:
        """Положить значение, вытеснив самую старую запись при переполнении."""

        with self._lock:
            self._entries[key] = {"value": value, "stored_at": self._clock()}
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_size:
                self._entries.popitem(last=False)
                self._evictions += 1
            if self.persist:
                self.save()

    def stats(self) -> Dict[str, int]:
        """``hits``/``misses``/``size``/``evictions``."""

        with self._lock:
            return {
                "hits": self._hits,
                "misses": self._misses,
                "size": len(self._entries),
                "evictions": self._evictions,
            }

    def hit_rate(self) -> float:
        with self._lock:
            total = self._hits + self._misses
            return (self._hits / total) if total else 0.0

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            if self.persist:
                self.save()

    def purge_expired(self) -> int:
        """Выкинуть истёкшие записи; вернуть их количество."""

        with self._lock:
            expired = [key for key, entry in self._entries.items() if self._is_expired(entry)]
            for key in expired:
                del self._entries[key]
                self._evictions += 1
            if expired and self.persist:
                self.save()
            return len(expired)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def __contains__(self, key: object) -> bool:
        with self._lock:
            entry = self._entries.get(str(key))
            return entry is not None and not self._is_expired(entry)

    # --- персистентность ---

    def load(self) -> int:
        """Поднять кеш с диска; битый файл просто игнорируется."""

        if self.path is None or not self.path.is_file():
            return 0
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return 0
        if not isinstance(payload, dict) or payload.get("version") != CACHE_VERSION:
            return 0
        entries = payload.get("entries")
        if not isinstance(entries, list):
            return 0
        loaded = 0
        with self._lock:
            for item in entries:
                if not isinstance(item, dict):
                    continue
                key = item.get("key")
                if not isinstance(key, str):
                    continue
                entry = {"value": item.get("value"), "stored_at": float(item.get("stored_at", 0.0))}
                if self._is_expired(entry):
                    continue
                self._entries[key] = entry
                loaded += 1
            while len(self._entries) > self.max_size:
                self._entries.popitem(last=False)
                self._evictions += 1
        return loaded

    def save(self) -> None:
        """Сбросить кеш на диск атомарно."""

        if self.path is None:
            return
        with self._lock:
            payload = {
                "version": CACHE_VERSION,
                "saved_at": self._clock(),
                "entries": [
                    {"key": key, "value": entry["value"], "stored_at": entry["stored_at"]}
                    for key, entry in self._entries.items()
                    if not self._is_expired(entry)
                ],
            }
        atomic_write_json(self.path, payload)

    # --- внутреннее ---

    def _is_expired(self, entry: Dict[str, Any]) -> bool:
        if self.ttl_sec <= 0:
            return False
        return (self._clock() - float(entry.get("stored_at", 0.0))) > self.ttl_sec
