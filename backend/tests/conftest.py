"""Shared test fixtures for high-level (HTTP/API) tests.

These fixtures spin up the real FastAPI app against an in-memory SQLite database (shared
across threads via a StaticPool, which is what FastAPI's TestClient needs), with `get_db`
overridden to that database. This lets tests exercise true end-to-end behaviour — routing,
auth, role gates, request/response schemas — not just service functions.

Service-level unit tests (test_extraction.py, test_enrichment.py, etc.) keep their own
self-contained SQLite fixtures; this conftest is for the API/flow and security suites.
"""

import uuid

import app.models  # noqa: F401  (registers every model on Base.metadata)
import pytest
from app.core.security import hash_password
from app.db.base import Base
from app.db.session import get_db
from app.models.user import User
from app.services.auth import create_user_session
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

TEST_PASSWORD = "test-pass-1234"


@pytest.fixture()
def default_password() -> str:
    return TEST_PASSWORD


@pytest.fixture()
def session_factory(request, tmp_path_factory):
    """In-memory SQLite shared across threads (TestClient runs requests off-thread).

    A test marked ``@pytest.mark.concurrent_db`` instead gets a file-based SQLite with the default
    connection pool, so genuinely-parallel requests each check out their own connection. The shared
    in-memory ``StaticPool`` is a single handle and cannot service true concurrency — SQLite raises
    ``bad parameter or other API misuse`` — which is a harness artifact, not product behaviour.
    """
    if request.node.get_closest_marker("concurrent_db"):
        db_path = tmp_path_factory.mktemp("concurrent-db") / "test.db"
        engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
        )
    else:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autocommit=False, autoflush=False)
    engine.dispose()


@pytest.fixture()
def db(session_factory):
    """A session for seeding/inspecting data directly (same DB the app uses)."""
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _rate_limit_fail_open(monkeypatch):
    """Run the API suite as if Redis were absent so the D1 rate-limit middleware fails open.

    The dev-stack Redis is reachable from the test container, but the D1 contract is that unit
    tests run without Redis (the limiter allows every request). Forcing the fail-open path keeps
    the suite deterministic and free of cross-test coupling through a shared live counter; the
    dedicated limiter tests inject their own fake client to exercise the enforced path.
    """
    from app.services import rate_limit

    monkeypatch.setattr(rate_limit, "_redis", lambda: None)
    rate_limit.reset_cache()


@pytest.fixture(autouse=True)
def _queue_capacity_fail_open(monkeypatch):
    """Run the API suite as if the queue depth were unmeasurable so the D39 guard fails open.

    Mirrors ``_rate_limit_fail_open``: the guard's contract is that it allows every request when
    Redis is unreachable, and unit tests run without Redis. Forcing the fail-open path keeps the
    suite deterministic and decoupled from any live queue state; the dedicated queue-cap tests
    monkeypatch the depth themselves to exercise the enforced path.
    """
    from app.workers import queue

    monkeypatch.setattr(queue, "pending_queue_depth", lambda: None)


@pytest.fixture(autouse=True)
def _reindex_runs_inline(monkeypatch):
    """Run the API suite as if Redis were absent so ``/search/reindex`` builds embeddings inline.

    D14 routes ``/search/reindex`` to the queued reindex job; when the queue is unavailable it falls
    back to a synchronous in-request build. The unit suite runs without Redis (mirroring
    ``_rate_limit_fail_open``/``_queue_capacity_fail_open``), and a background worker could not see a
    test's in-memory DB anyway, so force the synchronous fallback. Tests that assert the queued path
    monkeypatch ``enqueue_reindex`` back to a live id themselves.
    """
    from app.workers import queue

    monkeypatch.setattr(queue, "enqueue_reindex", lambda: None)


@pytest.fixture(autouse=True)
def _audit_file_sink_tmp(tmp_path, monkeypatch):
    """Redirect the append-only audit file sink (D31.1) to a throwaway path per test.

    The sink is on by default, so every ``record_event`` would otherwise append to the repo's
    ``./storage/audit/audit.jsonl`` during the suite. Point it at ``tmp_path`` so tests never touch
    the real volume; the dedicated sink test overrides this back to a path it inspects.
    """
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "audit_log_path", str(tmp_path / "audit.jsonl"))


@pytest.fixture()
def app(session_factory):
    from app.main import create_app

    application = create_app()

    def _override_get_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    application.dependency_overrides[get_db] = _override_get_db
    yield application
    application.dependency_overrides.clear()


@pytest.fixture()
def client(app):
    return TestClient(app)


@pytest.fixture()
def make_user(db):
    """Create a user row directly. Returns the User."""

    def _make(username: str, role: str = "reader", password: str = TEST_PASSWORD) -> User:
        user = User(username=username, password_hash=hash_password(password), role=role)
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    return _make


@pytest.fixture()
def auth_headers(db, make_user):
    """Return bearer-token headers for a freshly-created user of the given role."""

    def _headers(role: str = "owner", username: str | None = None) -> dict[str, str]:
        user = make_user(username or f"{role}-{uuid.uuid4().hex[:8]}", role=role)
        token, _session = create_user_session(db, user, ttl_minutes=60)
        db.commit()
        return {"Authorization": f"Bearer {token}"}

    return _headers
