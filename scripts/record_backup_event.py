#!/usr/bin/env python3
"""Record a backup/restore audit event from the Make/CLI backup path (SPEC §7.6, §10.2).

Backup and restore are operator-driven shell targets (see the Makefile) that run outside the
FastAPI process, so they have no natural ``record_event`` hook. This tiny CLI opens a database
session and writes one ``backup.*`` / ``restore.*`` audit event (also mirrored to the append-only
file sink), keeping the audit trail complete for the events the spec requires. Best-effort: it never
fails the surrounding backup/restore command.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.services.audit import record_event  # noqa: E402

ALLOWED_EVENTS = frozenset(
    {"backup.created", "backup.failed", "restore.completed", "restore.failed"}
)


def record_backup_event(event_type: str, *, artifact: str | None = None) -> None:
    """Persist a backup/restore audit event (system actor, no user)."""
    if event_type not in ALLOWED_EVENTS:
        raise ValueError(f"Unsupported backup/restore event: {event_type!r}")
    Base.metadata.create_all(bind=engine)
    details = {"method": "server_console"}
    if artifact:
        details["artifact"] = artifact
    with SessionLocal() as session:
        record_event(session, event_type, entity_type="backup", details=details)
        session.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Record a PaRacORD backup/restore audit event.")
    parser.add_argument("event_type", choices=sorted(ALLOWED_EVENTS))
    parser.add_argument("--artifact", default=None, help="Backup/restore artifact path or name.")
    args = parser.parse_args()
    try:
        record_backup_event(args.event_type, artifact=args.artifact)
    except Exception as exc:  # noqa: BLE001 - never fail the surrounding backup/restore command
        print(f"(audit event not recorded: {exc})", file=sys.stderr)
        return
    print(f"Recorded audit event {args.event_type!r}.")


if __name__ == "__main__":
    main()
