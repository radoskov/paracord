"""BoundedTTLCache (S10): LRU eviction + TTL expiry semantics."""

from app.utils.bounded_cache import BoundedTTLCache


def test_lru_eviction_beyond_maxsize() -> None:
    cache = BoundedTTLCache(maxsize=2, ttl_seconds=60)
    cache.set("a", 1)
    cache.set("b", 2)
    assert cache.get("a") == 1  # refresh a's recency
    cache.set("c", 3)  # evicts b (least recently used)
    assert cache.get("b") is None
    assert cache.get("a") == 1 and cache.get("c") == 3
    assert len(cache) == 2


def test_ttl_expiry(monkeypatch) -> None:
    from app.utils import bounded_cache as bc

    clock = {"now": 1000.0}
    monkeypatch.setattr(bc.time, "monotonic", lambda: clock["now"])
    cache = BoundedTTLCache(maxsize=8, ttl_seconds=10)
    cache.set("k", "v")
    assert cache.get("k") == "v"
    clock["now"] += 11
    assert cache.get("k", "gone") == "gone"
    assert len(cache) == 0  # the expired entry was dropped on read


def test_none_is_a_legitimate_cached_value() -> None:
    cache = BoundedTTLCache(maxsize=2, ttl_seconds=60)
    sentinel = object()
    cache.set("miss", None)
    assert cache.get("miss", sentinel) is None  # cached None, not the default


def test_clear() -> None:
    cache = BoundedTTLCache(maxsize=2, ttl_seconds=60)
    cache.set("a", 1)
    cache.clear()
    assert len(cache) == 0 and cache.get("a") is None
