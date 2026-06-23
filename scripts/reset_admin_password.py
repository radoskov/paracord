#!/usr/bin/env python3
"""Server-console credential recovery for PaperRacks.

This script is intentionally local/operator-driven. Do not expose equivalent functionality through
an unauthenticated web endpoint.
"""

import getpass
from pathlib import Path
import sys

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.security import hash_password  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.models.audit import AuditEvent  # noqa: E402
from app.models.user import User  # noqa: E402


def reset_password(username: str, password: str) -> User:
    """Reset a user's password and record the server-console audit event."""
    if not username:
        raise ValueError("Account username must not be empty")

    Base.metadata.create_all(bind=engine)
    with SessionLocal() as session:
        user = session.scalar(select(User).where(User.username == username))
        if user is None:
            raise RuntimeError(f"Account {username!r} was not found")

        user.password_hash = hash_password(password)
        session.add(
            AuditEvent(
                actor_user_id=user.id,
                event_type="auth.password_reset_cli",
                entity_type="user",
                entity_id=str(user.id),
                details={"method": "server_console", "sessions_revoked": False},
            )
        )
        session.commit()
        session.refresh(user)
        return user


def main() -> None:
    username = input("Account to reset: ").strip()
    password = getpass.getpass("New password: ")
    confirm = getpass.getpass("Confirm new password: ")
    if password != confirm:
        raise SystemExit("Passwords do not match")
    try:
        user = reset_password(username, password)
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Reset password for {user.username!r}; no session table exists yet to revoke.")


if __name__ == "__main__":
    main()
