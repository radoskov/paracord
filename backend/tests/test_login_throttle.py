"""Login-throttle Redis sliding window + fail-open fallback (D1).

Redis is not required to run the suite: the throttle falls back to a per-process dict when Redis is
unreachable, so a dead Redis can never lock everyone out. These tests drive the Redis path with a
tiny in-memory fake and confirm the fail-open path with the injectable clock.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.services import login_throttle


class _FakePipeline:
    def __init__(self, store: _FakeRedis) -> None:
        self._store = store
        self._ops: list[tuple] = []

    def zremrangebyscore(self, key, lo, hi):
        self._ops.append(("zremrangebyscore", key, lo, hi))
        return self

    def zrange(self, key, start, stop, withscores=False):
        self._ops.append(("zrange", key, start, stop, withscores))
        return self

    def zcard(self, key):
        self._ops.append(("zcard", key))
        return self

    def zadd(self, key, mapping):
        self._ops.append(("zadd", key, mapping))
        return self

    def expire(self, key, secs):
        self._ops.append(("expire", key, secs))
        return self

    def execute(self):
        return [self._store._run(op) for op in self._ops]


class _FakeRedis:
    def __init__(self) -> None:
        self._data: dict[str, dict[str, float]] = {}

    def pipeline(self):
        return _FakePipeline(self)

    def ping(self):
        return True

    def delete(self, *keys):
        removed = 0
        for key in keys:
            if self._data.pop(key, None) is not None:
                removed += 1
        return removed

    def scan_iter(self, match=""):
        prefix = match.rstrip("*")
        return [k for k in list(self._data) if k.startswith(prefix)]

    def _run(self, op):
        name = op[0]
        if name == "zremrangebyscore":
            _, key, lo, hi = op
            members = self._data.get(key, {})
            drop = [m for m, s in members.items() if lo <= s <= hi]
            for m in drop:
                del members[m]
            return len(drop)
        if name == "zrange":
            _, key, start, stop, withscores = op
            items = sorted(self._data.get(key, {}).items(), key=lambda kv: kv[1])
            end = None if stop == -1 else stop + 1
            sliced = items[start:end]
            return [(m, s) for m, s in sliced] if withscores else [m for m, _ in sliced]
        if name == "zcard":
            return len(self._data.get(op[1], {}))
        if name == "zadd":
            _, key, mapping = op
            self._data.setdefault(key, {}).update(mapping)
            return len(mapping)
        if name == "expire":
            return True
        raise AssertionError(f"unhandled op {name}")


@pytest.fixture
def fake_redis(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(login_throttle, "_redis", lambda: fake)
    login_throttle.reset_all()
    return fake


def test_redis_path_locks_after_max_failures(fake_redis):
    now = datetime(2026, 7, 2, 12, 0, 0, tzinfo=UTC)
    key = "alice"
    for i in range(3):
        count = login_throttle.record_failure(
            key, window_minutes=15, now=now + timedelta(seconds=i)
        )
    assert count == 3
    locked, retry = login_throttle.lock_state(
        key, max_failures=3, window_minutes=15, now=now + timedelta(seconds=3)
    )
    assert locked is True
    assert retry > 0


def test_redis_path_below_threshold_not_locked(fake_redis):
    now = datetime(2026, 7, 2, 12, 0, 0, tzinfo=UTC)
    login_throttle.record_failure("bob", window_minutes=15, now=now)
    locked, retry = login_throttle.lock_state("bob", max_failures=3, window_minutes=15, now=now)
    assert locked is False
    assert retry == 0


def test_redis_path_old_failures_age_out(fake_redis):
    start = datetime(2026, 7, 2, 12, 0, 0, tzinfo=UTC)
    for i in range(3):
        login_throttle.record_failure("carol", window_minutes=15, now=start + timedelta(seconds=i))
    # 16 minutes later every failure has aged out of the window.
    later = start + timedelta(minutes=16)
    locked, _ = login_throttle.lock_state("carol", max_failures=3, window_minutes=15, now=later)
    assert locked is False


def test_clear_removes_redis_key(fake_redis):
    now = datetime(2026, 7, 2, 12, 0, 0, tzinfo=UTC)
    login_throttle.record_failure("dave", window_minutes=15, now=now)
    login_throttle.record_failure("dave", window_minutes=15, now=now)
    login_throttle.clear("dave")
    locked, _ = login_throttle.lock_state("dave", max_failures=2, window_minutes=15, now=now)
    assert locked is False


def test_fails_open_to_in_process_when_redis_down(monkeypatch):
    monkeypatch.setattr(login_throttle, "_redis", lambda: None)
    login_throttle.reset_all()
    now = datetime(2026, 7, 2, 12, 0, 0, tzinfo=UTC)
    for i in range(3):
        login_throttle.record_failure("erin", window_minutes=15, now=now + timedelta(seconds=i))
    locked, retry = login_throttle.lock_state(
        "erin", max_failures=3, window_minutes=15, now=now + timedelta(seconds=3)
    )
    assert locked is True
    assert retry > 0
    login_throttle.reset_all()
