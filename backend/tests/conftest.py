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
def session_factory():
    """In-memory SQLite shared across threads (TestClient runs requests off-thread)."""
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
