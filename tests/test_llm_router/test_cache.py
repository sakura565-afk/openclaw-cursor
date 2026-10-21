"""Кеш: попадания/промахи, TTL, различение ключей, персистентность."""

from __future__ import annotations

import json
from pathlib import Path

from src.llm_router.cache import LRUTTLCache, hash_image, make_cache_key


class FakeClock:
    """Управляемое время, чтобы TTL проверялся без ожидания."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_cache_hit_and_miss_counters() -> None:
    cache = LRUTTLCache(max_size=4, ttl_sec=60.0, clock=FakeClock())

    assert cache.get("absent") is None
    cache.set("k", {"text": "ответ"})
    assert cache.get("k") == {"text": "ответ"}
    assert cache.get("k") == {"text": "ответ"}

    stats = cache.stats()
    assert stats == {"hits": 2, "misses": 1, "size": 1, "evictions": 0}
    assert cache.hit_rate() > 0.6


def test_cache_entry_expires_after_ttl() -> None:
    clock = FakeClock()
    cache = LRUTTLCache(max_size=4, ttl_sec=30.0, clock=clock)
    cache.set("k", "значение")

    clock.advance(29.0)
    assert cache.get("k") == "значение"

    clock.advance(2.0)
    assert cache.get("k") is None
    stats = cache.stats()
    assert stats["size"] == 0
    assert stats["evictions"] == 1
    assert stats["misses"] == 1


def test_cache_key_depends_on_model_effort_and_image() -> None:
    base = make_cache_key("prompt", "MiniMax-M3.1-Flash", "low")

    assert base != make_cache_key("prompt", "MiniMax-Vision-32B", "low")
    assert base != make_cache_key("prompt", "MiniMax-M3.1-Flash", "high")
    assert base != make_cache_key("prompt", "MiniMax-M3.1-Flash", "low", image=b"png-bytes")
    assert base == make_cache_key("prompt", "MiniMax-M3.1-Flash", "low")

    # Один и тот же промпт с разными картинками обязан давать разные ключи.
    first = make_cache_key("что на картинке", "MiniMax-Vision-32B", "vision", image=b"a")
    second = make_cache_key("что на картинке", "MiniMax-Vision-32B", "vision", image=b"b")
    assert first != second
    assert make_cache_key(
        "что на картинке", "MiniMax-Vision-32B", "vision", image_hash=hash_image(b"a")
    ) == first


def test_cache_evicts_least_recently_used() -> None:
    cache = LRUTTLCache(max_size=2, ttl_sec=0.0, clock=FakeClock())
    cache.set("a", 1)
    cache.set("b", 2)
    cache.get("a")  # «a» становится свежим
    cache.set("c", 3)

    assert cache.get("b") is None
    assert cache.get("a") == 1
    assert cache.get("c") == 3
    assert cache.stats()["evictions"] == 1


def test_cache_persists_atomically(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "llm_cache.json"
    clock = FakeClock()
    cache = LRUTTLCache(max_size=4, ttl_sec=600.0, path=path, persist=True, clock=clock)
    cache.set("k", {"text": "из кеша"})

    assert path.is_file()
    assert not list(path.parent.glob("*.tmp"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["entries"][0]["key"] == "k"

    revived = LRUTTLCache(max_size=4, ttl_sec=600.0, path=path, persist=True, clock=clock)
    assert revived.get("k") == {"text": "из кеша"}

    # Истёкшие записи при загрузке не поднимаются.
    clock.advance(10_000)
    expired = LRUTTLCache(max_size=4, ttl_sec=600.0, path=path, persist=False, clock=clock)
    assert expired.get("k") is None
