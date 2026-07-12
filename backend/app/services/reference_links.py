"""Helpers for the canonical-reference model (batch 12).

A :class:`~app.models.citation.Reference` is now a **shared** canonical record; the per-citing-work
edges live in :class:`~app.models.citation.ReferenceCitation`. These helpers centralize the two
things every call site needs: computing a reference's ``dedup_key`` and going between a work and its
references through the link table (so the old ``Reference.citing_work_id == work_id`` reads have one
obvious replacement).
"""

from __future__ import annotations

import uuid

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.models.citation import Reference, ReferenceCitation
from app.services.duplicate_detection import split_arxiv_id
from app.utils.normalization import arxiv_base_from_doi, normalize_doi, normalize_title

# ``Reference.dedup_key`` is ``String(512)`` and indexed. A real-world reference can carry a very long
# (or mis-parsed, whole-citation-as-title) title, so the title-based key must be capped or the value
# overflows the column (Postgres raises "value too long", failing extraction *and* the 0059 backfill
# migration). Truncating the title portion is safe: the key stays deterministic and two references
# whose titles agree for the first ~500 chars are, for dedup purposes, the same work.
_MAX_DEDUP_KEY_LEN = 512


def reference_dedup_key(
    *,
    doi: str | None = None,
    arxiv_id: str | None = None,
    normalized_title: str | None = None,
    year: int | None = None,
) -> str | None:
    """Stable identity for deduping a reference: normalized DOI → arXiv base → title+year.
    An arXiv DOI (10.48550/arXiv.<id>) keys as its arXiv base, not as a DOI.

    Returns ``None`` when there is neither an identifier nor a usable title — such a reference
    carries no signal to dedup on, so it is always stored as its own row.
    """
    if doi:
        nd = normalize_doi(doi)
        # An arXiv DOI (10.48550/arXiv.<id>) and a bare arXiv id spell the same paper — key both
        # as ``arxiv:<base>`` so they consolidate to one canonical reference row.
        arxiv_base = arxiv_base_from_doi(nd) if nd else None
        if arxiv_base:
            return f"arxiv:{arxiv_base}"
        if nd:
            return f"doi:{nd}"
    if arxiv_id:
        base = split_arxiv_id(arxiv_id)["base"]
        if base:
            return f"arxiv:{base}"
    if normalized_title:
        year_part = str(year) if year is not None else ""
        # Cap the title so the whole key fits the indexed String(512) column.
        budget = _MAX_DEDUP_KEY_LEN - len("title:") - len("|") - len(year_part)
        title_part = normalized_title[:budget] if budget > 0 else ""
        return f"title:{title_part}|{year_part}"
    return None


def find_or_create_reference(
    db: Session,
    *,
    title: str | None,
    doi: str | None,
    arxiv_id: str | None,
    year: int | None,
    raw_citation: str | None,
    authors: list[str] | None,
) -> Reference:
    """Return the canonical reference for these fields, creating one if none exists.

    Dedup is by :func:`reference_dedup_key`; a sparse existing row is enriched (never clobbered) with
    newly-available authors/year. Resolution state is left untouched — matching (a separate step)
    owns it.
    """
    nt = normalize_title(title) if title else None
    nt = nt or None  # an all-punctuation title normalizes to "" → treat as absent
    key = reference_dedup_key(doi=doi, arxiv_id=arxiv_id, normalized_title=nt, year=year)
    existing: Reference | None = None
    if key is not None:
        # Rows written before the arXiv-DOI bridge keyed an arXiv DOI as ``doi:10.48550/arxiv.<b>``;
        # keep finding them so no duplicate canonical row is created for live data.
        keys = [key]
        if key.startswith("arxiv:"):
            keys.append(f"doi:10.48550/arxiv.{key.removeprefix('arxiv:')}")
        existing = db.scalars(
            select(Reference)
            .where(Reference.dedup_key.in_(keys))
            .order_by(Reference.created_at, Reference.id)
        ).first()
    if existing is not None:
        if authors and not existing.authors:
            existing.authors = authors
        if year and existing.year is None:
            existing.year = year
        return existing
    reference = Reference(
        raw_citation=raw_citation,
        title=title,
        normalized_title=nt,
        doi=normalize_doi(doi) if doi else None,
        arxiv_id=arxiv_id,
        year=year,
        authors=authors or None,
        dedup_key=key,
        resolution_status="unresolved",
    )
    db.add(reference)
    db.flush()
    return reference


def citing_work_ids_subquery(work_ids: list[uuid.UUID] | set[uuid.UUID]) -> Select:
    """A subquery of reference ids cited by any of ``work_ids`` (replaces ``citing_work_id IN``)."""
    return select(ReferenceCitation.reference_id).where(
        ReferenceCitation.citing_work_id.in_(list(work_ids))
    )


def references_for_work(db: Session, work_id: uuid.UUID) -> list[Reference]:
    """All canonical references cited by ``work_id``, oldest link first (stable order)."""
    return list(
        db.scalars(
            select(Reference)
            .join(ReferenceCitation, ReferenceCitation.reference_id == Reference.id)
            .where(ReferenceCitation.citing_work_id == work_id)
            .order_by(ReferenceCitation.created_at, Reference.created_at)
        ).all()
    )
