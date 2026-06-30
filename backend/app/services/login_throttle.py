"""In-process failed-login throttling (SPEC §7.2 auth hardening).

A sliding window of recent failures keyed by username (lower-cased). After ``max_failures`` within
``window_minutes`` the key is locked until the oldest in-window failure ages out. State is
per-process, which suits the single-node local-first deployment; it resets on restart (fail-open,
never fail-closed). The clock is injectable so tests are deterministic.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from datetime import UTC, datetime, timedelta

_failures: dict[str, list[datetime]] = defaultdict(list)
_lock = threading.Lock()


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


def _prune(key: str, *, window: timedelta, now: datetime) -> list[datetime]:
    cutoff = now - window
    kept = [t for t in _failures[key] if t > cutoff]
    if kept:
        _failures[key] = kept
    else:
        _failures.pop(key, None)
    return kept


def lock_state(
    key: str, *, max_failures: int, window_minutes: int, now: datetime | None = None
) -> tuple[bool, int]:
    """Return ``(locked, retry_after_seconds)`` for a key without recording a new failure."""
    now = _now(now)
    window = timedelta(minutes=window_minutes)
    with _lock:
        attempts = _prune(key, window=window, now=now)
        if len(attempts) < max_failures:
            return False, 0
        unlock_at = attempts[0] + window
        return True, max(1, int((unlock_at - now).total_seconds()))


def record_failure(key: str, *, window_minutes: int, now: datetime | None = None) -> int:
    """Record a failed attempt; return the count of failures currently in the window."""
    now = _now(now)
    window = timedelta(minutes=window_minutes)
    with _lock:
        _prune(key, window=window, now=now)
        _failures[key].append(now)
        return len(_failures[key])


def clear(key: str) -> None:
    """Clear a key's failures (call on a successful login)."""
    with _lock:
        _failures.pop(key, None)


def reset_all() -> None:
    """Wipe all throttle state (test helper)."""
    with _lock:
        _failures.clear()
