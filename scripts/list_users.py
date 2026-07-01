#!/usr/bin/env python3
"""List PaRacORD user accounts from the server console (SPEC §7.3 recovery tooling).

This script must run on the server PC or inside the backend container with database access.
It is read-only: it reuses the owner-operated ``user_service.list_users`` and prints a summary.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.services.users import list_users  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="List PaRacORD user accounts (server console).")
    parser.parse_args()

    Base.metadata.create_all(bind=engine)
    with SessionLocal() as session:
        users = list_users(session)

    if not users:
        print("No user accounts found.")
        return

    print(f"{'USERNAME':<32} {'ROLE':<12} {'STATUS':<10} CREATED")
    for user in users:
        status = "disabled" if getattr(user, "disabled_at", None) else "active"
        created = user.created_at.isoformat() if user.created_at else "-"
        print(f"{user.username:<32} {str(user.role):<12} {status:<10} {created}")
    print(f"\n{len(users)} account(s).")


if __name__ == "__main__":
    main()
