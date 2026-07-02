#!/usr/bin/env python3
"""Idempotently ensure a dedicated end-to-end (Playwright) test user exists.

Companion to ``bootstrap_admin.py``: rather than creating the single, immutable owner, this creates
an ordinary **admin** test account with well-known credentials so the browser E2E suite can sign in.
It reuses the in-app ``app.services.users.create_user`` helper (which also seeds the user's personal
group + default grants and writes audit events), acting as the existing owner.

Safe to re-run: if the user already exists it is a no-op (the password is NOT reset, so a rerun mid
test-session never invalidates an open browser session).

Run inside the backend container::

    docker compose exec -T api python scripts/ensure_e2e_user.py

Credentials come from the environment (with dev defaults):

* ``E2E_USERNAME`` (default ``e2e_admin``)
* ``E2E_PASSWORD`` (default ``e2e-Passw0rd!``)
"""

import os
import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.security import Role  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.users import create_user  # noqa: E402

DEFAULT_USERNAME = "e2e_admin"
DEFAULT_PASSWORD = "e2e-Passw0rd!"  # pragma: allowlist secret (deliberate dev/test default)


def ensure_e2e_user(username: str, password: str) -> User:
    """Create the E2E admin test user if missing; return the existing/created account.

    Idempotent: an already-present user is returned untouched (no password reset).
    """
    if not username:
        raise ValueError("E2E username must not be empty")
    if not password:
        raise ValueError("E2E password must not be empty")

    Base.metadata.create_all(bind=engine)
    with SessionLocal() as session:
        existing = session.scalar(select(User).where(User.username == username))
        if existing is not None:
            return existing

        # create_user requires an actor; creating an admin is owner-only, so act as the owner.
        owner = session.scalar(select(User).where(User.role == Role.OWNER))
        if owner is None:
            raise RuntimeError(
                "No owner account exists yet — run scripts/bootstrap_admin.py first."
            )

        user = create_user(
            session,
            username=username,
            password=password,
            role=str(Role.ADMIN),
            actor=owner,
        )
        session.commit()
        session.refresh(user)
        return user


def main() -> None:
    username = os.environ.get("E2E_USERNAME", DEFAULT_USERNAME).strip() or DEFAULT_USERNAME
    password = os.environ.get("E2E_PASSWORD", DEFAULT_PASSWORD) or DEFAULT_PASSWORD
    try:
        user = ensure_e2e_user(username, password)
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(f"E2E test user ready: {user.username!r} (role={user.role})")


if __name__ == "__main__":
    main()
