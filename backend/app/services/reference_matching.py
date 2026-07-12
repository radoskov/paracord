"""Reference→library matching — the "likely local" matcher (batch 12).

Links an extracted bibliography :class:`~app.models.citation.Reference` to the library
:class:`~app.models.work.Work` it most likely *is*, so references for papers already in the library
stop showing as "external" (and their Import stops creating near-duplicates).

Pipeline (owner decisions D1/D2, §Workplan batch 12):

* **Identifier gate (D2) is authoritative.** If the reference carries a DOI/arXiv id, a work with
  the *same* normalized identifier is a hard match (``via="identifier"``). A candidate whose
  identifier of the same kind *differs* is disqualified from the fuzzy fallback — an occasional
  workshop-vs-journal false positive is far less annoying than the current mass of false
  "external/missing" flags. Only when identifiers are absent on one/both sides does fuzzy run.
* **Fuzzy title(+year+author).** ``similarity_pct`` over ``canonical_title`` (D1), first-token
  blocking (``_blocking_key``), an equal-year gate when both years are present, and (Phase 4) an
  author-overlap gate that is skipped when either side lists no authors.

Persistence follows the batch-12 status rules (item #4): a **confirmed** match is locked (never
touched by a rescan); a **rejected** candidate is never re-proposed (though a *different, better*
candidate may still surface); a fuzzy guess is written to ``suggested_work_id``/``match_score`` (a
soft ``likely_match``) and is **never** promoted to ``resolved_work_id`` unless the operator's
``fuzzy_as_confirmed`` toggle is on — that column drives ref→ref edges and metrics, so a wrong guess
must not corrupt them.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.citation import Reference
from app.models.metadata import MetadataAssertion
from app.models.work import Work
from app.services.author_matching import author_overlap_ratio
from app.services.citation_graph import _identifier_keys
from app.services.duplicate_detection import _blocking_key, split_arxiv_id
from app.utils.normalization import normalize_doi, normalize_title, similarity_pct

# Statuses set/left by the matcher. A confirmed match is locked; a rejected candidate is remembered.
_LOCKED_STATUS = "confirmed_match"


@dataclass
class ReferenceMatch:
    """The best local work a reference likely is, with how it was found."""

    work_id: uuid.UUID
    score: float  # 0-100 (100 for an identifier match)
    via: str  # "identifier" | "fuzzy"


def _ref_identifier_keys(reference: Reference) -> set[str]:
    return set(_identifier_keys(doi=reference.doi, arxiv_id=reference.arxiv_id))


def _work_identifier_keys(work: Work) -> set[str]:
    return set(_identifier_keys(doi=work.doi, arxiv_id=work.arxiv_id))


def _work_arxiv_base(work: Work) -> str | None:
    if work.arxiv_base_id:
        return work.arxiv_base_id
    return split_arxiv_id(work.arxiv_id)["base"] if work.arxiv_id else None


def _identifier_gate_ok(reference: Reference, work: Work, *, gate_enabled: bool) -> bool:
    """A candidate is disqualified when an identifier present on *both* sides differs (D2)."""
    if not gate_enabled:
        return True
    if reference.doi and work.doi and normalize_doi(reference.doi) != normalize_doi(work.doi):
        return False
    ref_base = split_arxiv_id(reference.arxiv_id)["base"] if reference.arxiv_id else None
    work_base = _work_arxiv_base(work)
    return not (ref_base and work_base and ref_base != work_base)


def _year_ok(reference: Reference, work: Work, settings: Settings) -> bool:
    if not settings.reference_matching_require_year_match:
        return True
    if reference.year is not None and work.year is not None:
        return reference.year == work.year
    return True


def _author_ok(db: Session, reference: Reference, work: Work, settings: Settings) -> bool:
    """Author-overlap gate (Phase 4), skipped when either side lists no authors."""
    ref_authors = reference.authors or []
    if not ref_authors:
        return True  # a signal we can't compute can't disqualify
    work_authors = _work_author_names(db, work.id)
    if not work_authors:
        return True
    ratio = author_overlap_ratio(ref_authors, work_authors)
    return ratio >= settings.reference_matching_author_threshold


def _work_author_names(db: Session, work_id: uuid.UUID) -> list[str]:
    """A work's canonical author display names, from its best ``authors`` MetadataAssertion.

    Work authors are provenance rows (``field_name="authors"``, a ``"; "``-joined value), not a
    column — mirrors ``export_service._authors_by_work`` (canonical, then confidence).
    """
    value = db.scalars(
        select(MetadataAssertion.value)
        .where(
            MetadataAssertion.entity_type == "work",
            MetadataAssertion.entity_id == work_id,
            MetadataAssertion.field_name == "authors",
        )
        .order_by(
            MetadataAssertion.selected_as_canonical.desc(),
            func.coalesce(MetadataAssertion.confidence, 0).desc(),
        )
    ).first()
    return [name.strip() for name in (value or "").split(";") if name.strip()]


def _works_by_identifier(db: Session, reference: Reference) -> list[Work]:
    conditions = []
    if reference.doi:
        conditions.append(func.lower(Work.doi) == normalize_doi(reference.doi))
    base = split_arxiv_id(reference.arxiv_id)["base"] if reference.arxiv_id else None
    if base:
        conditions.append(Work.arxiv_base_id == base)
    if not conditions:
        return []
    return list(db.scalars(select(Work).where(or_(*conditions))).all())


def _blocking_candidates(db: Session, normalized_title: str) -> list[Work]:
    block = _blocking_key(normalized_title)
    if not block:
        return []
    return list(
        db.scalars(
            select(Work).where(
                or_(
                    Work.normalized_title == block,
                    Work.normalized_title.like(block.replace("%", r"\%") + " %"),
                )
            )
        ).all()
    )


def find_reference_match(
    db: Session,
    reference: Reference,
    *,
    settings: Settings | None = None,
    candidate_works: Sequence[Work] | None = None,
) -> ReferenceMatch | None:
    """Best local work this reference likely is, or ``None``.

    ``candidate_works`` restricts matching to a specific set — the reverse-rescan on a newly-created
    work passes just that one work (cheap); when ``None`` the whole library is searched (identifier
    index + title blocking). Merged shadows are never a match target.
    """
    settings = settings or get_settings()
    if not settings.reference_matching_enabled:
        return None

    gate = settings.reference_matching_identifier_gate
    ref_keys = _ref_identifier_keys(reference) if gate else set()

    # Stage A — identifier exact match (authoritative, rescan-safe).
    if ref_keys:
        works = (
            candidate_works if candidate_works is not None else _works_by_identifier(db, reference)
        )
        for work in works:
            if work.merged_into_id is not None:
                continue
            if ref_keys & _work_identifier_keys(work):
                return ReferenceMatch(work_id=work.id, score=100.0, via="identifier")

    # Stage B — fuzzy title(+year+author).
    ref_title = reference.title
    nt = reference.normalized_title or (normalize_title(ref_title) if ref_title else None)
    if not ref_title or not nt:
        return None
    works = candidate_works if candidate_works is not None else _blocking_candidates(db, nt)
    best: ReferenceMatch | None = None
    for work in works:
        if work.merged_into_id is not None or not work.canonical_title:
            continue
        if not _identifier_gate_ok(reference, work, gate_enabled=gate):
            continue
        score = similarity_pct(ref_title, work.canonical_title)
        if score < settings.reference_matching_title_threshold:
            continue
        if not _year_ok(reference, work, settings):
            continue
        if not _author_ok(db, reference, work, settings):
            continue
        if best is None or score > best.score:
            best = ReferenceMatch(work_id=work.id, score=score, via="fuzzy")
    return best


# --------------------------------------------------------------------------------------------------
# Persistence — apply a match to a reference per the batch-12 status rules.
# --------------------------------------------------------------------------------------------------


def _set_local(reference: Reference, match: ReferenceMatch) -> bool:
    changed = (
        reference.resolved_work_id != match.work_id or reference.resolution_status != "local_match"
    )
    reference.resolved_work_id = match.work_id
    reference.suggested_work_id = None
    reference.match_score = match.score
    reference.resolution_status = "local_match"
    return changed


def _set_likely(reference: Reference, match: ReferenceMatch) -> bool:
    changed = (
        reference.suggested_work_id != match.work_id
        or reference.resolution_status != "likely_match"
        or reference.resolved_work_id is not None
    )
    reference.resolved_work_id = None
    reference.suggested_work_id = match.work_id
    reference.match_score = match.score
    reference.resolution_status = "likely_match"
    return changed


def _clear_to(reference: Reference, status: str) -> bool:
    changed = (
        reference.resolved_work_id is not None
        or reference.suggested_work_id is not None
        or reference.resolution_status != status
    )
    reference.resolved_work_id = None
    reference.suggested_work_id = None
    reference.match_score = None
    reference.resolution_status = status
    return changed


def resolve_and_persist(
    db: Session,
    reference: Reference,
    *,
    settings: Settings | None = None,
    fuzzy_as_confirmed: bool = False,
    candidate_works: Sequence[Work] | None = None,
) -> bool:
    """Run the matcher for one reference and persist the outcome. Returns whether it changed.

    Never touches a ``confirmed_match`` (locked by the user) and never re-proposes the *same*
    candidate the user already rejected (a different, better candidate may still surface). A soft
    fuzzy guess stays in ``suggested_work_id`` unless ``fuzzy_as_confirmed`` is on.
    """
    settings = settings or get_settings()
    status = reference.resolution_status
    if status == _LOCKED_STATUS:
        return False

    match = find_reference_match(db, reference, settings=settings, candidate_works=candidate_works)

    if match is not None and match.via == "identifier":
        return _set_local(reference, match)

    if match is not None:  # fuzzy
        if status == "rejected_match" and reference.suggested_work_id == match.work_id:
            # Keep the rejection, but refresh the displayed score.
            if reference.match_score != match.score:
                reference.match_score = match.score
                return True
            return False
        if fuzzy_as_confirmed:
            return _set_local(reference, match)
        return _set_likely(reference, match)

    # No candidate found.
    if status == "rejected_match":
        return False  # keep the user's rejection + its remembered suggestion
    has_content = bool(reference.title or reference.doi or reference.arxiv_id)
    return _clear_to(reference, "external" if has_content else "unresolved")


def run_matching_for_references(
    db: Session,
    references: Iterable[Reference],
    *,
    settings: Settings | None = None,
    fuzzy_as_confirmed: bool = False,
) -> int:
    """Match+persist a batch of references (e.g. every reference an extraction touched). Returns the
    number whose resolution changed."""
    settings = settings or get_settings()
    changed = 0
    for reference in references:
        if resolve_and_persist(
            db, reference, settings=settings, fuzzy_as_confirmed=fuzzy_as_confirmed
        ):
            changed += 1
    return changed


def rescan_references_for_new_work(
    db: Session,
    work: Work,
    *,
    settings: Settings | None = None,
    fuzzy_as_confirmed: bool = False,
) -> int:
    """Reverse-rescan: check the library's not-yet-resolved references against *one* new work.

    This is what fixes the owner's backlog without re-extracting every paper — when a work is
    created (manual/import/from-reference), only the references that could plausibly match it (same
    normalized-DOI, or sharing its title blocking key) are re-run, against just that work.
    """
    settings = settings or get_settings()
    if not settings.reference_matching_enabled:
        return 0

    conditions = []
    if work.doi:
        conditions.append(Reference.doi == normalize_doi(work.doi))
    if work.normalized_title:
        block = _blocking_key(work.normalized_title)
        if block:
            conditions.append(Reference.normalized_title == block)
            conditions.append(Reference.normalized_title.like(block.replace("%", r"\%") + " %"))
    if not conditions:
        return 0

    candidates = db.scalars(
        select(Reference).where(
            Reference.resolved_work_id.is_(None),
            Reference.resolution_status != _LOCKED_STATUS,
            or_(*conditions),
        )
    ).all()

    changed = 0
    for reference in candidates:
        if resolve_and_persist(
            db,
            reference,
            settings=settings,
            fuzzy_as_confirmed=fuzzy_as_confirmed,
            candidate_works=[work],
        ):
            changed += 1
    return changed
