#!/usr/bin/env python3
"""Revoke a user's active sessions from the server console (SPEC §7.3 recovery tooling).

This mirrors the session-revocation the password-reset performs, without changing the password —
use it to force-log-out a compromised or stuck account. Runs on the server PC or inside the backend
container with database access. It is intentionally local/operator-driven: do NOT expose equivalent
functionality through an unauthenticated web endpoint.
"""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, update

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.models.audit import AuditEvent  # noqa: E402
from app.models.session import UserSession  # noqa: E402
from app.models.user import User  # noqa: E402


def revoke_sessions(username: str) -> tuple[User, int]:
    """Revoke every active session for ``username`` and record a server-console audit event."""
    if not username:
        raise ValueError("Account username must not be empty")

    Base.metadata.create_all(bind=engine)
    with SessionLocal() as session:
        user = session.scalar(select(User).where(User.username == username))
        if user is None:
            raise RuntimeError(f"Account {username!r} was not found")

        revoked = session.execute(
            update(UserSession)
            .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        session.add(
            AuditEvent(
                actor_user_id=user.id,
                event_type="auth.sessions_revoked_cli",
                entity_type="user",
                entity_id=str(user.id),
                details={
                    "method": "server_console",
                    "sessions_revoked": True,
                    "sessions_revoked_count": revoked.rowcount,
                },
            )
        )
        session.commit()
        return user, revoked.rowcount


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Revoke all active sessions for a PaRacORD account (server console)."
    )
    parser.add_argument("username", nargs="?", help="Account username to revoke sessions for.")
    args = parser.parse_args()

    username = args.username or input("Account to revoke sessions for: ").strip()
    try:
        user, count = revoke_sessions(username)
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Revoked {count} active session(s) for {user.username!r}.")


if __name__ == "__main__":
    main()
