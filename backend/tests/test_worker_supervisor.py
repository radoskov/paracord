"""RQ worker supervisor launch/shutdown logic (D1).

Exercises the "how many workers to launch" decision and the shutdown/kill handling without actually
spawning ``rq worker`` child processes.
"""

from __future__ import annotations

import subprocess

from app.workers import supervisor


class _FakePopen:
    def __init__(self, *, ignores_terminate: bool = False) -> None:
        self.pid = 4321
        self.returncode = None
        self._alive = True
        self._ignores_terminate = ignores_terminate
        self.terminated = False
        self.killed = False

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self.terminated = True
        if not self._ignores_terminate:
            self._alive = False

    def wait(self, timeout=None):
        if self._alive:
            raise subprocess.TimeoutExpired(cmd="rq", timeout=timeout)
        return 0

    def kill(self):
        self.killed = True
        self._alive = False


def test_resolve_worker_count_uses_config(monkeypatch):
    from app.db import session as db_session
    from app.services import app_config

    class _FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(db_session, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(app_config, "effective_rq_worker_count", lambda db: 4)
    assert supervisor.resolve_worker_count() == 4


def test_resolve_worker_count_clamps_to_at_least_one(monkeypatch):
    from app.db import session as db_session
    from app.services import app_config

    class _FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(db_session, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(app_config, "effective_rq_worker_count", lambda db: 0)
    assert supervisor.resolve_worker_count() == 1


def test_resolve_worker_count_falls_back_on_db_error(monkeypatch):
    from app.db import session as db_session

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(db_session, "SessionLocal", _boom)
    assert supervisor.resolve_worker_count() == supervisor._DEFAULT_RQ_WORKER_COUNT


def test_worker_command_targets_queue_and_redis():
    cmd = supervisor._worker_command()
    assert cmd[:3] == ["rq", "worker", "--url"]
    assert cmd[-1] == supervisor.QUEUE_NAME


def test_start_spawns_configured_number(monkeypatch):
    spawned: list[_FakePopen] = []

    def _fake_spawn():
        child = _FakePopen()
        spawned.append(child)
        return child

    monkeypatch.setattr(supervisor, "_spawn", _fake_spawn)
    sup = supervisor._Supervisor(3)
    sup.start()
    assert len(sup.children) == 3
    assert len(spawned) == 3


def test_shutdown_terminates_children():
    sup = supervisor._Supervisor(2)
    sup.children = [_FakePopen(), _FakePopen()]
    sup.shutdown()
    assert all(c.terminated for c in sup.children)
    assert not any(c.killed for c in sup.children)


def test_shutdown_kills_children_that_ignore_terminate():
    sup = supervisor._Supervisor(1)
    stubborn = _FakePopen(ignores_terminate=True)
    sup.children = [stubborn]
    sup.shutdown()
    assert stubborn.terminated
    assert stubborn.killed


# --- D10: gate worker startup on migrations being at head ---


def test_migrations_at_head_true_when_db_matches_scripts(monkeypatch):
    monkeypatch.setattr(supervisor, "_alembic_script_heads", lambda: {"0037_default_shelf"})
    monkeypatch.setattr(supervisor, "_alembic_db_heads", lambda: {"0037_default_shelf"})
    assert supervisor.migrations_at_head() is True


def test_migrations_at_head_false_when_db_behind(monkeypatch):
    monkeypatch.setattr(supervisor, "_alembic_script_heads", lambda: {"0037_default_shelf"})
    monkeypatch.setattr(supervisor, "_alembic_db_heads", lambda: {"0036_embedding_model_registry"})
    assert supervisor.migrations_at_head() is False


def test_migrations_at_head_false_when_db_empty(monkeypatch):
    monkeypatch.setattr(supervisor, "_alembic_script_heads", lambda: {"0037_default_shelf"})
    monkeypatch.setattr(supervisor, "_alembic_db_heads", lambda: set())
    assert supervisor.migrations_at_head() is False


def test_wait_for_migrations_returns_true_when_at_head(monkeypatch):
    monkeypatch.setattr(supervisor, "migrations_at_head", lambda: True)
    assert supervisor.wait_for_migrations(timeout=5.0, interval=0.01) is True


def test_wait_for_migrations_fails_open_after_timeout(monkeypatch):
    monkeypatch.setattr(supervisor, "migrations_at_head", lambda: False)
    # timeout=0 → the deadline is already reached, so it returns False without sleeping.
    assert supervisor.wait_for_migrations(timeout=0.0, interval=0.01) is False


def test_wait_for_migrations_keeps_waiting_when_db_probe_raises(monkeypatch):
    calls = {"n": 0}

    def _flaky() -> bool:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("db not ready")
        return True

    monkeypatch.setattr(supervisor, "migrations_at_head", _flaky)
    assert supervisor.wait_for_migrations(timeout=5.0, interval=0.001) is True
    assert calls["n"] == 3
