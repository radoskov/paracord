"""A small bounded, TTL'd, in-process cache (S10 / register F3b).

Replaces the module-level unbounded dicts in citation_summary / visualization /
external_preview. Deliberately in-process (each gunicorn worker holds its own copy — fine at
this deployment's scale); the *bound* is the point: without it every distinct key ever requested
stays resident for the process lifetime.

Semantics: LRU eviction beyond ``maxsize``; entries expire ``ttl_seconds`` after being set
(reads do not refresh the TTL — staleness is bounded even for hot keys). ``get`` returns
``default`` for a missing/expired key; a stored value of ``None`` is a legitimate cached value
(remembered miss), so callers that store ``None`` must pass their own sentinel default.
Thread-safe (FastAPI sync endpoints run in a thread pool).
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any


class BoundedTTLCache:
    """LRU + TTL cache. See module docstring for semantics."""

    def __init__(self, maxsize: int, ttl_seconds: float) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be >= 1")
        self._maxsize = maxsize
        self._ttl = float(ttl_seconds)
        self._lock = threading.Lock()
        # key -> (expires_at_monotonic, value); insertion/access order = LRU order.
        self._data: OrderedDict[Any, tuple[float, Any]] = OrderedDict()

    def get(self, key: Any, default: Any = None) -> Any:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return default
            expires_at, value = entry
            if expires_at <= time.monotonic():
                del self._data[key]
                return default
            self._data.move_to_end(key)
            return value

    def set(self, key: Any, value: Any) -> None:
        with self._lock:
            self._data[key] = (time.monotonic() + self._ttl, value)
            self._data.move_to_end(key)
            while len(self._data) > self._maxsize:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def __contains__(self, key: Any) -> bool:  # pragma: no cover - convenience
        sentinel = object()
        return self.get(key, sentinel) is not sentinel
