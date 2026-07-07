"""Shared fixtures for the deeper adversarial safety battery (Batch S).

These build on the HTTP/API fixtures in ``backend/tests/conftest.py`` (``client``, ``db``,
``make_user``, ``auth_headers``). The whole directory is marked ``@pytest.mark.safety`` (see the
package-level ``pytestmark`` re-applied per module) so ``make test``/``make test-full`` never run it;
only ``make test-safety`` (``pytest -m safety``) does.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from app.models.organization import Rack, Shelf, ShelfWork
from app.models.user import User
from app.models.work import Work
from app.services.auth import create_user_session


@pytest.fixture()
def headers_for(db) -> Callable[[User], dict[str, str]]:
    """Return a factory that mints bearer-token headers for an existing user row."""

    def _headers(user: User) -> dict[str, str]:
        token, _session = create_user_session(db, user, ttl_minutes=60)
        db.commit()
        return {"Authorization": f"Bearer {token}"}

    return _headers


@pytest.fixture()
def make_shelf(db) -> Callable[..., Shelf]:
    def _make(name: str | None = None, *, access_level: str = "open") -> Shelf:
        shelf = Shelf(name=name or f"s-{uuid.uuid4().hex[:8]}", access_level=access_level)
        db.add(shelf)
        db.commit()
        db.refresh(shelf)
        return shelf

    return _make


@pytest.fixture()
def make_rack(db) -> Callable[..., Rack]:
    def _make(name: str | None = None, *, access_level: str = "open") -> Rack:
        rack = Rack(name=name or f"r-{uuid.uuid4().hex[:8]}", access_level=access_level)
        db.add(rack)
        db.commit()
        db.refresh(rack)
        return rack

    return _make


@pytest.fixture()
def make_work(db) -> Callable[..., Work]:
    def _make(title: str | None = None, *, created_by: uuid.UUID | None = None) -> Work:
        work = Work(
            canonical_title=title or f"w-{uuid.uuid4().hex[:8]}", created_by_user_id=created_by
        )
        db.add(work)
        db.commit()
        db.refresh(work)
        return work

    return _make


@pytest.fixture()
def add_to_shelf(db) -> Callable[[Shelf, Work], None]:
    def _add(shelf: Shelf, work: Work) -> None:
        db.add(ShelfWork(shelf_id=shelf.id, work_id=work.id))
        db.commit()

    return _add


@pytest.fixture()
def hidden_work(db, make_shelf, make_work, add_to_shelf) -> Callable[..., Work]:
    """Create a work placed ONLY on a private shelf → invisible to a plain reader."""

    def _make(title: str | None = None) -> Work:
        work = make_work(title or f"secret-{uuid.uuid4().hex[:8]}")
        private = make_shelf(access_level="private")
        add_to_shelf(private, work)
        return work

    return _make
