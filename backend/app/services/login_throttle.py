"""Failed-login throttling (SPEC §7.2 auth hardening).

A sliding window of recent failures keyed by username (lower-cased). After ``max_failures`` within
``window_minutes`` the key is locked until the oldest in-window failure ages out.

State is shared across API workers via Redis (a per-key sorted set of failure timestamps). If Redis
is unreachable the store falls back to a per-process dict, so a dead Redis can never lock everyone
out (fail-open, never fail-closed). The clock is injectable so tests are deterministic.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_KEY_PREFIX = "paracord:login-throttle:"

_failures: dict[str, list[datetime]] = defaultdict(list)
_lock = threading.Lock()

# Per-url memo of the Redis client so we don't build a fresh connection pool on every login.
_clients: dict[str, object] = {}


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


def _redis():
    """Return a live Redis client, or ``None`` when Redis is unreachable (fail-open)."""
    url = get_settings().redis_url
    client = _clients.get(url)
    if client is None:
        try:
            from redis import Redis

            client = Redis.from_url(url)
            _clients[url] = client
        except Exception as exc:  # noqa: BLE001 - fall back to the in-process store
            logger.debug(
                "Login throttle: Redis client unavailable (%s); using in-process store", exc
            )
            return None
    try:
        client.ping()
    except Exception as exc:  # noqa: BLE001 - dead Redis: fail open to the in-process store
        logger.debug("Login throttle: Redis unreachable (%s); using in-process store", exc)
        return None
    return client


# --- in-process fallback -----------------------------------------------------


def _prune(key: str, *, window: timedelta, now: datetime) -> list[datetime]:
    cutoff = now - window
    kept = [t for t in _failures[key] if t > cutoff]
    if kept:
        _failures[key] = kept
    else:
        _failures.pop(key, None)
    return kept


def _local_lock_state(
    key: str, *, max_failures: int, window: timedelta, now: datetime
) -> tuple[bool, int]:
    with _lock:
        attempts = _prune(key, window=window, now=now)
        if len(attempts) < max_failures:
            return False, 0
        unlock_at = attempts[0] + window
        return True, max(1, int((unlock_at - now).total_seconds()))


def _local_record_failure(key: str, *, window: timedelta, now: datetime) -> int:
    with _lock:
        _prune(key, window=window, now=now)
        _failures[key].append(now)
        return len(_failures[key])


# --- Redis sliding window ----------------------------------------------------


def _redis_lock_state(
    client, key: str, *, max_failures: int, window: timedelta, now: datetime
) -> tuple[bool, int]:
    rkey = f"{_KEY_PREFIX}{key}"
    cutoff = (now - window).timestamp()
    pipe = client.pipeline()
    pipe.zremrangebyscore(rkey, 0, cutoff)
    pipe.zrange(rkey, 0, 0, withscores=True)
    pipe.zcard(rkey)
    _, oldest, count = pipe.execute()
    if count < max_failures:
        return False, 0
    oldest_score = oldest[0][1] if oldest else now.timestamp()
    unlock_at = oldest_score + window.total_seconds()
    return True, max(1, int(unlock_at - now.timestamp()))


def _redis_record_failure(client, key: str, *, window: timedelta, now: datetime) -> int:
    rkey = f"{_KEY_PREFIX}{key}"
    cutoff = (now - window).timestamp()
    member = f"{now.timestamp():.6f}-{uuid.uuid4().hex}"
    pipe = client.pipeline()
    pipe.zremrangebyscore(rkey, 0, cutoff)
    pipe.zadd(rkey, {member: now.timestamp()})
    pipe.expire(rkey, int(window.total_seconds()) + 1)
    pipe.zcard(rkey)
    results = pipe.execute()
    return int(results[-1])


# --- public API --------------------------------------------------------------


def lock_state(
    key: str, *, max_failures: int, window_minutes: int, now: datetime | None = None
) -> tuple[bool, int]:
    """Return ``(locked, retry_after_seconds)`` for a key without recording a new failure."""
    now = _now(now)
    window = timedelta(minutes=window_minutes)
    client = _redis()
    if client is not None:
        try:
            return _redis_lock_state(client, key, max_failures=max_failures, window=window, now=now)
        except Exception as exc:  # noqa: BLE001 - fail open to the in-process store
            logger.warning("Login throttle: Redis lock_state failed (%s); using in-process", exc)
    return _local_lock_state(key, max_failures=max_failures, window=window, now=now)


def record_failure(key: str, *, window_minutes: int, now: datetime | None = None) -> int:
    """Record a failed attempt; return the count of failures currently in the window."""
    now = _now(now)
    window = timedelta(minutes=window_minutes)
    client = _redis()
    if client is not None:
        try:
            return _redis_record_failure(client, key, window=window, now=now)
        except Exception as exc:  # noqa: BLE001 - fail open to the in-process store
            logger.warning(
                "Login throttle: Redis record_failure failed (%s); using in-process", exc
            )
    return _local_record_failure(key, window=window, now=now)


def clear(key: str) -> None:
    """Clear a key's failures (call on a successful login)."""
    client = _redis()
    if client is not None:
        try:
            client.delete(f"{_KEY_PREFIX}{key}")
        except Exception as exc:  # noqa: BLE001 - best effort; also clear in-process
            logger.warning("Login throttle: Redis clear failed (%s)", exc)
    with _lock:
        _failures.pop(key, None)


def reset_all() -> None:
    """Wipe all throttle state (test helper) — the in-process store and any Redis keys."""
    with _lock:
        _failures.clear()
    client = _redis()
    if client is not None:
        try:
            keys = list(client.scan_iter(match=f"{_KEY_PREFIX}*"))
            if keys:
                client.delete(*keys)
        except Exception as exc:  # noqa: BLE001 - best effort
            logger.debug("Login throttle: Redis reset_all skipped (%s)", exc)
