"""Shared request rate limiting (D1 overload protection).

A Redis fixed-window counter enforced as ASGI middleware. Two scopes are checked per request: a
**per-client** window (keyed by the caller's bearer token when present, else the client IP) and a
**global** window (one key for every request). Exceeding either ceiling rejects the request with a
429 + ``Retry-After``.

State lives in Redis so the ceilings are shared across API workers. When Redis is unreachable the
limiter **fails open** (allows the request) — a dead Redis must never take the API down. Unit tests
run without Redis and therefore exercise the fail-open path unchanged.

The editable ceilings come from the owner-managed ``app_config`` singleton (per-client and global
requests per minute), read through a short in-process TTL cache so a healthy Redis path doesn't add
a DB round-trip per request. The DB is only consulted when Redis is actually reachable.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_KEY_PREFIX = "paracord:ratelimit:"

# Paths that must never be throttled: liveness probes and the API docs/schema.
_EXEMPT_PREFIXES = (
    "/api/v1/health",
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/v1/openapi.json",
)

# Per-url memo of the Redis client so we don't rebuild a connection pool on every request.
_clients: dict[str, object] = {}

# Short-lived cache of the effective ceilings so a healthy Redis path doesn't hit the DB per request.
_CONFIG_TTL_SECONDS = 5.0
_config_cache: dict[str, object] = {"at": 0.0, "limits": None}


@dataclass
class RateLimitDecision:
    """Outcome of a rate-limit check."""

    allowed: bool
    scope: str | None = None  # "client" | "global" | None
    retry_after: int = 0


def is_exempt(path: str) -> bool:
    """Return True for paths that must never be rate limited (health, docs, schema)."""
    return any(path == prefix or path.startswith(prefix) for prefix in _EXEMPT_PREFIXES)


def _redis():
    """Return a live Redis client, or ``None`` when Redis is unreachable (fail-open)."""
    from app.core.config import get_settings

    url = get_settings().redis_url
    client = _clients.get(url)
    if client is None:
        try:
            from redis import Redis

            client = Redis.from_url(url)
            _clients[url] = client
        except Exception as exc:  # noqa: BLE001 - fail open: allow the request
            logger.debug("Rate limit: Redis client unavailable (%s); allowing request", exc)
            return None
    try:
        client.ping()
    except Exception as exc:  # noqa: BLE001 - dead Redis: fail open
        logger.debug("Rate limit: Redis unreachable (%s); allowing request", exc)
        return None
    return client


def _effective_limits() -> tuple[int, int]:
    """Return the (per-client, global) per-minute ceilings, cached briefly to avoid a DB hit/req."""
    now = time.monotonic()
    cached = _config_cache.get("limits")
    if cached is not None and now - float(_config_cache["at"]) < _CONFIG_TTL_SECONDS:
        return cached  # type: ignore[return-value]
    from app.models.app_config import (
        _DEFAULT_RATE_LIMIT_GLOBAL_PER_MIN,
        _DEFAULT_RATE_LIMIT_PER_CLIENT_PER_MIN,
    )

    limits = (_DEFAULT_RATE_LIMIT_PER_CLIENT_PER_MIN, _DEFAULT_RATE_LIMIT_GLOBAL_PER_MIN)
    try:
        from app.db.session import SessionLocal
        from app.services import app_config

        with SessionLocal() as db:
            limits = (
                app_config.effective_rate_limit_per_client_per_min(db),
                app_config.effective_rate_limit_global_per_min(db),
            )
    except Exception as exc:  # noqa: BLE001 - fall back to the built-in defaults
        logger.debug("Rate limit: could not read effective ceilings (%s); using defaults", exc)
    _config_cache["at"] = now
    _config_cache["limits"] = limits
    return limits


def _client_id(*, token: str | None, ip: str | None) -> str:
    """Per-client bucket id: the bearer token (hashed) when present, else the client IP."""
    if token:
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
        return f"tok:{digest}"
    return f"ip:{ip or 'unknown'}"


def _retry_after(now: float) -> int:
    """Seconds until the current fixed minute-window rolls over."""
    return max(1, 60 - int(now % 60))


def _incr_window(client, scope_key: str, *, now: float) -> int:
    """Increment (and expire) the fixed one-minute window counter; return the new count."""
    window = int(now // 60)
    rkey = f"{_KEY_PREFIX}{scope_key}:{window}"
    pipe = client.pipeline()
    pipe.incr(rkey)
    pipe.expire(rkey, 120)
    count, _ = pipe.execute()
    return int(count)


def check(
    *,
    token: str | None,
    ip: str | None,
    now: float | None = None,
) -> RateLimitDecision:
    """Count this request against the per-client + global windows; fail open when Redis is down.

    The per-client counter is incremented first: if it exceeds its ceiling the request is rejected
    without touching the global counter, so a single noisy client can't inflate the global window.
    """
    client = _redis()
    if client is None:
        return RateLimitDecision(allowed=True)
    now = time.time() if now is None else now
    try:
        per_client_limit, global_limit = _effective_limits()
        client_key = f"client:{_client_id(token=token, ip=ip)}"
        if _incr_window(client, client_key, now=now) > per_client_limit:
            return RateLimitDecision(allowed=False, scope="client", retry_after=_retry_after(now))
        if _incr_window(client, "global", now=now) > global_limit:
            return RateLimitDecision(allowed=False, scope="global", retry_after=_retry_after(now))
        return RateLimitDecision(allowed=True)
    except Exception as exc:  # noqa: BLE001 - fail open: allow the request
        logger.warning("Rate limit: check failed (%s); allowing request", exc)
        return RateLimitDecision(allowed=True)


def reset_cache() -> None:
    """Clear the in-process ceiling cache (test helper / post-config-change invalidation)."""
    _config_cache["at"] = 0.0
    _config_cache["limits"] = None
