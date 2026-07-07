"""Read/write helpers for the citation-summary missing-work worklist (Track C C3a).

Thin persistence layer over :class:`app.models.citation_worklist.MissingWorkDecision`: list a user's
decisions, upsert one (``import``/``ignore``), and clear one. Decisions are keyed by the stable
normalized missing-work key from :mod:`app.services.citation_summary`, so they persist across a
summary recompute. Callers own the transaction (these helpers ``flush`` but do not ``commit``).
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.citation_worklist import MissingWorkDecision

# Accepted decision values for a missing-work worklist entry.
DECISIONS = ("import", "ignore")


def list_decisions(db: Session, user_id: uuid.UUID) -> dict[str, str]:
    """Return ``{missing_key: decision}`` for one user's recorded decisions."""
    rows = db.execute(
        select(MissingWorkDecision.missing_key, MissingWorkDecision.decision).where(
            MissingWorkDecision.user_id == user_id
        )
    ).all()
    return {key: decision for key, decision in rows}


def set_decision(
    db: Session, user_id: uuid.UUID, missing_key: str, decision: str
) -> MissingWorkDecision:
    """Upsert a user's decision for ``missing_key`` (idempotent per (user, key))."""
    if decision not in DECISIONS:
        raise ValueError(f"Unsupported decision: {decision}")
    row = db.scalar(
        select(MissingWorkDecision).where(
            MissingWorkDecision.user_id == user_id,
            MissingWorkDecision.missing_key == missing_key,
        )
    )
    if row is None:
        row = MissingWorkDecision(user_id=user_id, missing_key=missing_key, decision=decision)
        db.add(row)
    else:
        row.decision = decision
    db.flush()
    return row


def clear_decision(db: Session, user_id: uuid.UUID, missing_key: str) -> bool:
    """Delete a user's decision for ``missing_key``; return whether a row was removed."""
    result = db.execute(
        delete(MissingWorkDecision).where(
            MissingWorkDecision.user_id == user_id,
            MissingWorkDecision.missing_key == missing_key,
        )
    )
    db.flush()
    return bool(result.rowcount)


__all__ = ["DECISIONS", "list_decisions", "set_decision", "clear_decision"]
