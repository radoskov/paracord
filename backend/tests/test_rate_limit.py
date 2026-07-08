"""Shared Redis rate limiting (D1 overload protection).

The limiter fails open when Redis is unreachable (the default in the unit-test environment), so the
rest of the suite is unaffected. These tests inject a tiny in-memory fake Redis to exercise the
enforced path: per-client 429, global 429, a ``Retry-After`` hint, and fail-open when the client
raises.
"""

from __future__ import annotations

import pytest
from app.services import rate_limit


class _FakePipeline:
    def __init__(self, store: _FakeRedis) -> None:
        self._store = store
        self._ops: list[tuple] = []

    def incr(self, key):
        self._ops.append(("incr", key))
        return self

    def expire(self, key, secs):
        self._ops.append(("expire", key, secs))
        return self

    def execute(self):
        out = []
        for op in self._ops:
            if op[0] == "incr":
                self._store.counters[op[1]] = self._store.counters.get(op[1], 0) + 1
                out.append(self._store.counters[op[1]])
            else:
                out.append(True)
        return out


class _FakeRedis:
    def __init__(self) -> None:
        self.counters: dict[str, int] = {}

    def ping(self):
        return True

    def pipeline(self):
        return _FakePipeline(self)


class _BrokenRedis(_FakeRedis):
    def pipeline(self):
        raise RuntimeError("redis exploded mid-request")


@pytest.fixture
def fake_limiter(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(rate_limit, "_redis", lambda: fake)
    monkeypatch.setattr(rate_limit, "_effective_limits", lambda: (3, 100))
    return fake


def test_per_client_429_after_limit(fake_limiter):
    now = 1_000_000.0
    for _ in range(3):
        assert rate_limit.check(token=None, ip="10.0.0.1", now=now).allowed is True
    blocked = rate_limit.check(token=None, ip="10.0.0.1", now=now)
    assert blocked.allowed is False
    assert blocked.scope == "client"
    assert blocked.retry_after > 0


def test_distinct_clients_have_separate_windows(fake_limiter):
    now = 1_000_000.0
    for _ in range(3):
        rate_limit.check(token=None, ip="10.0.0.1", now=now)
    # A different client (by IP) is unaffected by the first client's exhausted window.
    assert rate_limit.check(token=None, ip="10.0.0.2", now=now).allowed is True
    # And a bearer token keys a bucket distinct from any IP.
    assert rate_limit.check(token="sometoken", ip="10.0.0.1", now=now).allowed is True


def test_global_429(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(rate_limit, "_redis", lambda: fake)
    # Generous per-client ceiling, tiny global ceiling: distinct clients still trip the global cap.
    monkeypatch.setattr(rate_limit, "_effective_limits", lambda: (1000, 2))
    now = 2_000_000.0
    assert rate_limit.check(token=None, ip="1.1.1.1", now=now).allowed is True
    assert rate_limit.check(token=None, ip="2.2.2.2", now=now).allowed is True
    blocked = rate_limit.check(token=None, ip="3.3.3.3", now=now)
    assert blocked.allowed is False
    assert blocked.scope == "global"
    assert blocked.retry_after > 0


def test_window_rolls_over(fake_limiter):
    now = 3_000_000.0
    for _ in range(3):
        rate_limit.check(token=None, ip="9.9.9.9", now=now)
    assert rate_limit.check(token=None, ip="9.9.9.9", now=now).allowed is False
    # A minute later the fixed window key changes, so the client is allowed again.
    assert rate_limit.check(token=None, ip="9.9.9.9", now=now + 61).allowed is True


def test_fails_open_when_redis_unreachable(monkeypatch):
    monkeypatch.setattr(rate_limit, "_redis", lambda: None)
    assert rate_limit.check(token=None, ip="10.0.0.1", now=1.0).allowed is True


def test_fails_open_when_client_raises(monkeypatch):
    monkeypatch.setattr(rate_limit, "_redis", lambda: _BrokenRedis())
    monkeypatch.setattr(rate_limit, "_effective_limits", lambda: (1, 1))
    # A Redis error mid-request must never reject the request.
    assert rate_limit.check(token=None, ip="10.0.0.1", now=1.0).allowed is True


# --- E1: opt-in fail-closed (PARACORD_PRODUCTION_REQUIRE_REDIS) --------------


def test_fails_closed_when_require_redis_and_redis_unreachable(monkeypatch):
    monkeypatch.setattr(rate_limit, "_redis", lambda: None)
    monkeypatch.setattr(rate_limit, "_require_redis", lambda: True)
    decision = rate_limit.check(token=None, ip="10.0.0.1", now=1.0)
    assert decision.allowed is False
    assert decision.scope == "unavailable"


def test_fails_closed_when_client_raises_and_require_redis(monkeypatch):
    monkeypatch.setattr(rate_limit, "_redis", lambda: _BrokenRedis())
    monkeypatch.setattr(rate_limit, "_effective_limits", lambda: (1, 1))
    monkeypatch.setattr(rate_limit, "_require_redis", lambda: True)
    decision = rate_limit.check(token=None, ip="10.0.0.1", now=1.0)
    assert decision.allowed is False
    assert decision.scope == "unavailable"


def test_middleware_returns_503_when_require_redis_and_redis_down(client, monkeypatch):
    monkeypatch.setattr(rate_limit, "_redis", lambda: None)
    monkeypatch.setattr(rate_limit, "_require_redis", lambda: True)
    resp = client.get("/api/v1/auth/me")  # not exempt
    assert resp.status_code == 503
    assert "Retry-After" in resp.headers
    assert "detail" in resp.json()


def test_middleware_returns_429_with_retry_after(client, monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(rate_limit, "_redis", lambda: fake)
    monkeypatch.setattr(rate_limit, "_effective_limits", lambda: (2, 100))
    # /auth/me is not exempt; unauthenticated it 401s, but the request still counts.
    for _ in range(2):
        assert client.get("/api/v1/auth/me").status_code == 401
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 429
    assert int(resp.headers["Retry-After"]) > 0
    assert "detail" in resp.json()


def test_health_endpoint_is_exempt(client, monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(rate_limit, "_redis", lambda: fake)
    monkeypatch.setattr(rate_limit, "_effective_limits", lambda: (1, 1))
    # Well past any ceiling, but health is never throttled.
    for _ in range(5):
        assert client.get("/api/v1/health").status_code == 200
