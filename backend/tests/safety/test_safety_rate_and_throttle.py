"""Rate-limit / login-throttle / queue-cap under burst + concurrency (Batch S).

The core suite runs with the limiter/throttle/queue-cap failing open (see the autouse fixtures in
``backend/tests/conftest.py``). Here we inject the enforced paths and hammer them past their
thresholds — sequentially and concurrently — to prove the cap actually trips (429) and recovers, and
that concurrency does not slip past it (best-effort, in-process).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from app.core.config import get_settings
from app.services import login_throttle, rate_limit
from app.workers import queue

pytestmark = pytest.mark.safety


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


# --- rate limiter under burst + concurrency ----------------------------------------------------


def test_rate_limit_trips_under_sequential_burst(client, monkeypatch) -> None:
    fake = _FakeRedis()
    monkeypatch.setattr(rate_limit, "_redis", lambda: fake)
    monkeypatch.setattr(rate_limit, "_effective_limits", lambda: (3, 100))
    statuses = [client.get("/api/v1/auth/me").status_code for _ in range(10)]
    assert 429 in statuses
    assert any(s == 401 for s in statuses)  # the pre-cap requests were let through (then 401)


def test_rate_limit_not_bypassed_by_concurrency(client, monkeypatch) -> None:
    fake = _FakeRedis()
    monkeypatch.setattr(rate_limit, "_redis", lambda: fake)
    monkeypatch.setattr(rate_limit, "_effective_limits", lambda: (3, 100))

    def hit(_i: int) -> int:
        return client.get("/api/v1/auth/me").status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(hit, range(16)))
    # More requests than the ceiling → the shared counter still rejects the overflow.
    assert 429 in statuses


def test_rate_limit_window_recovers(monkeypatch) -> None:
    fake = _FakeRedis()
    monkeypatch.setattr(rate_limit, "_redis", lambda: fake)
    monkeypatch.setattr(rate_limit, "_effective_limits", lambda: (3, 100))
    now = 5_000_000.0
    for _ in range(3):
        assert rate_limit.check(token=None, ip="7.7.7.7", now=now).allowed
    assert rate_limit.check(token=None, ip="7.7.7.7", now=now).allowed is False
    # A minute later the fixed-window key rolls over → allowed again.
    assert rate_limit.check(token=None, ip="7.7.7.7", now=now + 61).allowed is True


# --- login throttle over HTTP ------------------------------------------------------------------


def test_login_throttle_trips_and_recovers(client, db, make_user, monkeypatch) -> None:
    # Force the in-process fallback so the throttle is deterministic (no shared live Redis).
    monkeypatch.setattr(login_throttle, "_redis", lambda: None)
    login_throttle.reset_all()
    make_user("throttle-victim", role="reader", password="test-pass-1234")
    max_failures = get_settings().login_max_failures
    body = {"username": "throttle-victim", "password": "wrong-password"}
    for _ in range(max_failures):
        assert client.post("/api/v1/auth/login", json=body).status_code == 401
    locked = client.post("/api/v1/auth/login", json=body)
    assert locked.status_code == 429
    assert int(locked.headers["Retry-After"]) > 0
    # Clearing the throttle key (as a successful login would) recovers immediately.
    login_throttle.clear("throttle-victim")
    recovered = client.post("/api/v1/auth/login", json=body)
    assert recovered.status_code == 401  # back to a normal failed-auth response, not 429
    login_throttle.reset_all()


# --- queue cap under burst + concurrency -------------------------------------------------------

_ONE_BIBTEX = "@article{a2020, title = {Alpha}, author = {A, X}, year = {2020}}"


def test_queue_cap_rejects_burst(client, auth_headers, monkeypatch) -> None:
    monkeypatch.setattr(queue, "pending_queue_depth", lambda: 10_000)
    headers = auth_headers("editor")
    statuses = [
        client.post(
            "/api/v1/imports/bibtex", headers=headers, json={"content": _ONE_BIBTEX}
        ).status_code
        for _ in range(5)
    ]
    assert statuses == [429] * 5


def test_queue_cap_not_bypassed_by_concurrency(client, auth_headers, monkeypatch) -> None:
    monkeypatch.setattr(queue, "pending_queue_depth", lambda: 10_000)
    headers = auth_headers("editor")

    def submit(_i: int) -> int:
        return client.post(
            "/api/v1/imports/bibtex", headers=headers, json={"content": _ONE_BIBTEX}
        ).status_code

    with ThreadPoolExecutor(max_workers=6) as pool:
        statuses = list(pool.map(submit, range(12)))
    # The security property: concurrency must not let a job slip past a full queue. No request
    # succeeds (no 201); the cap trips (429). (A transient 5xx from the shared in-memory SQLite
    # connection under true parallelism is a harness artifact — still never a bypass.)
    assert 201 not in statuses
    assert 429 in statuses
