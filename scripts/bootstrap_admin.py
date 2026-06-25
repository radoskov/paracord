#!/usr/bin/env python3
"""Create the first PaRacORD owner account.

This script must run on the server PC or inside the backend container with database access.
"""

import getpass
import sys
from pathlib import Path

from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.security import Role, hash_password  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.models.audit import AuditEvent  # noqa: E402
from app.models.user import User  # noqa: E402


def create_first_owner(username: str, password: str) -> User:
    """Create the first owner account, refusing to overwrite an existing owner."""
    if not username:
        raise ValueError("Owner username must not be empty")

    Base.metadata.create_all(bind=engine)
    with SessionLocal() as session:
        owner_count = session.scalar(
            select(func.count()).select_from(User).where(User.role == Role.OWNER)
        )
        if owner_count:
            raise RuntimeError("An owner account already exists")
        if session.scalar(select(User).where(User.username == username)):
            raise RuntimeError(f"User {username!r} already exists")

        owner = User(username=username, password_hash=hash_password(password), role=Role.OWNER)
        session.add(owner)
        session.flush()
        session.add(
            AuditEvent(
                actor_user_id=owner.id,
                event_type="user.created",
                entity_type="user",
                entity_id=str(owner.id),
                details={"method": "server_console_bootstrap", "role": Role.OWNER},
            )
        )
        session.commit()
        session.refresh(owner)
        return owner


def main() -> None:
    username = input("Owner username: ").strip()
    password = getpass.getpass("Owner password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        raise SystemExit("Passwords do not match")
    try:
        owner = create_first_owner(username, password)
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Created owner account for {owner.username!r}")


if __name__ == "__main__":
    main()
