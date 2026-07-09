"""Shared helpers for the ``uq_works_doi`` collision (issue 6 / batch 8).

A work's DOI is unique (the partial unique index ``uq_works_doi``). When extraction, enrichment, a
manual edit, or a metadata-apply tries to give a paper a DOI that already belongs to a *different*
paper, the write fails at flush/commit with an ``IntegrityError``. These helpers turn that into a
clear, actionable message that names the offending DOI and — when it can be resolved — the paper
that already holds it, instead of a raw stack dump / HTTP 500.

The module is framework-agnostic: callers decide how to surface the message (a worker re-raises a
``RuntimeError``; an API endpoint raises ``HTTPException(409)``).
"""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.work import Work

_DOI_CONFLICT_CONSTRAINT = "uq_works_doi"
_DETAIL_DOI_RE = re.compile(r"\(doi\)=\(([^)]*)\)")

# Leading + trailing halves of the message; the offending DOI and existing-paper title (when known)
# are spliced in between so the actionable identifiers come before the "resolve it" instruction.
_MESSAGE_HEAD = (
    "This paper's DOI already belongs to another paper in the library (likely a duplicate)."
)
_MESSAGE_TAIL = "Resolve the duplicate, then retry."


def doi_conflict_detail(exc: Exception) -> str | None:
    """Return the Postgres DETAIL string if ``exc`` is a ``uq_works_doi`` unique violation, else None.

    Recognises the specific "this DOI already belongs to another paper" collision so it can be
    handled with a clear message — without swallowing other integrity errors, which must still
    surface loudly.
    """
    orig = getattr(exc, "orig", None)
    diag = getattr(orig, "diag", None)
    if getattr(diag, "constraint_name", None) != _DOI_CONFLICT_CONSTRAINT:
        return None
    return getattr(diag, "message_detail", None) or "DOI already exists on another paper"


def doi_from_detail(detail: str | None) -> str | None:
    """Extract the offending DOI value from a Postgres unique-violation DETAIL string.

    The DETAIL looks like ``Key (doi)=(10.1234/foo) already exists.``; returns ``10.1234/foo``.
    """
    match = _DETAIL_DOI_RE.search(detail or "")
    return match.group(1) if match else None


def conflict_message(db: Session, *, doi: str | None) -> str:
    """Build the user-facing collision message, naming the DOI and the existing paper if resolvable.

    Best-effort: the existing-paper lookup never raises (the caller has usually just rolled back a
    failed transaction, so the session must still yield a clean, usable message).
    """
    parts = [_MESSAGE_HEAD]
    if doi:
        parts.append(f"Offending DOI: {doi}.")
        try:
            existing = db.scalar(select(Work).where(Work.doi == doi))
            if existing is not None and existing.canonical_title:
                parts.append(f'It already belongs to: "{existing.canonical_title}".')
        except Exception:  # noqa: BLE001 - message enrichment is best-effort, never fatal
            pass
    parts.append(_MESSAGE_TAIL)
    return " ".join(parts)


def message_from_exception(db: Session, exc: Exception) -> str | None:
    """Convenience: ``conflict_message`` derived straight from a caught ``IntegrityError``.

    Returns None when ``exc`` is not a DOI collision (so the caller re-raises the original error).
    """
    detail = doi_conflict_detail(exc)
    if detail is None:
        return None
    return conflict_message(db, doi=doi_from_detail(detail))
