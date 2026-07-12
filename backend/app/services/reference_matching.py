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
* **Fuzzy title(+year+author).** ``title_similarity_pct`` over ``canonical_title`` (D1),
  stopword-tolerant first-content-token blocking (``_relaxed_blocking_key``/``_block_conditions``),
  a ±``year_tolerance`` year gate when both years are present (preprint vs published drift), and
  (Phase 4) an author-overlap gate that is skipped when either side lists no authors. arXiv DOIs
  (``10.48550/arXiv.…``) are bridged to bare arXiv ids in both the exact stage and the gate.

Persistence follows the batch-12 status rules (item #4): a **confirmed** match is locked (never
touched by a rescan); a **rejected** candidate is never re-proposed (though a *different, better*
candidate may still surface); a fuzzy guess is written to ``suggested_work_id``/``match_score`` (a
soft ``likely_match``) and is **never** promoted to ``resolved_work_id`` unless the operator's
``fuzzy_as_confirmed`` toggle is on — that column drives ref→ref edges and metrics, so a wrong guess
must not corrupt them.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.citation import Reference
from app.models.metadata import MetadataAssertion
from app.models.work import Work
from app.services.author_matching import author_overlap_ratio
from app.services.citation_graph import _identifier_keys
from app.services.duplicate_detection import split_arxiv_id
from app.utils.normalization import (
    arxiv_base_from_doi,
    normalize_doi,
    normalize_title,
    title_similarity_pct,
)

# Statuses set/left by the matcher. A confirmed match is locked; a rejected candidate is remembered.
_LOCKED_STATUS = "confirmed_match"


@dataclass
class ReferenceMatch:
    """The best local work a reference likely is, with how it was found."""

    work_id: uuid.UUID
    score: float  # 0-100 (100 for an identifier match)
    via: str  # "identifier" | "fuzzy"


@dataclass
class MatchFields:
    """The identifier/title/author fields the matcher reads, decoupled from the Reference model.

    Lets other citation-shaped rows (an :class:`~app.models.external_citation.ExternalPaper`
    citing paper, an import candidate) run through the exact same matching algorithm and gates as a
    bibliography reference — same F1 behavior in both directions.
    """

    title: str | None = None
    normalized_title: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    year: int | None = None
    authors: list[str] | None = None


# Everything the matcher reads is present on both Reference and MatchFields (duck-typed).
Matchable = Reference | MatchFields


def _ref_identifier_keys(reference: Matchable) -> set[str]:
    return set(_identifier_keys(doi=reference.doi, arxiv_id=reference.arxiv_id))


def _work_identifier_keys(work: Work) -> set[str]:
    return set(_identifier_keys(doi=work.doi, arxiv_id=work.arxiv_id))


def _work_arxiv_base(work: Work) -> str | None:
    if work.arxiv_base_id:
        return work.arxiv_base_id
    return split_arxiv_id(work.arxiv_id)["base"] if work.arxiv_id else None


def _arxiv_base_of(*, arxiv_id: str | None, doi: str | None) -> str | None:
    """Effective arXiv base id from a bare arXiv id or an arXiv DOI (10.48550/arXiv.<id>)."""
    base = split_arxiv_id(arxiv_id)["base"] if arxiv_id else None
    if not base and doi:
        base = arxiv_base_from_doi(doi)
    return base


def _identifier_gate_ok(reference: Matchable, work: Work, *, gate_enabled: bool) -> bool:
    """A candidate is disqualified when an identifier present on *both* sides differs (D2).

    An arXiv DOI is compared as an *arXiv id*, not as a DOI: the preprint (arXiv DOI) and the
    published version (journal DOI) of the same paper legitimately carry different DOIs, so a
    DOI-kind mismatch involving an arXiv DOI must not disqualify the fuzzy fallback.
    """
    if not gate_enabled:
        return True
    ref_doi = normalize_doi(reference.doi) if reference.doi else None
    work_doi = normalize_doi(work.doi) if work.doi else None
    if (
        ref_doi
        and work_doi
        and ref_doi != work_doi
        and not arxiv_base_from_doi(ref_doi)
        and not arxiv_base_from_doi(work_doi)
    ):
        return False
    ref_base = _arxiv_base_of(arxiv_id=reference.arxiv_id, doi=reference.doi)
    work_base = _work_arxiv_base(work) or (arxiv_base_from_doi(work.doi) if work.doi else None)
    return not (ref_base and work_base and ref_base != work_base)


def _year_ok(reference: Matchable, work: Work, settings: Settings) -> bool:
    if not settings.reference_matching_require_year_match:
        return True
    if reference.year is not None and work.year is not None:
        # Tolerate a small offset: preprint vs published year (and citation-style year drift)
        # routinely differ by one while still being the same paper.
        return abs(reference.year - work.year) <= settings.reference_matching_year_tolerance
    return True


def _author_ok(
    db: Session,
    reference: Matchable,
    work: Work,
    settings: Settings,
    author_names: Mapping[uuid.UUID, list[str]] | None = None,
) -> bool:
    """Author-overlap gate (Phase 4), skipped when either side lists no authors.

    ``author_names`` is an optional prebuilt work-id → names map (the batch rescan job builds it
    once instead of one query per fuzzy candidate — S8); when absent, the DB is queried per work.
    """
    ref_authors = reference.authors or []
    if not ref_authors:
        return True  # a signal we can't compute can't disqualify
    work_authors = (
        author_names.get(work.id, [])
        if author_names is not None
        else _work_author_names(db, work.id)
    )
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


def _works_by_identifier(db: Session, reference: Matchable) -> list[Work]:
    conditions = []
    if reference.doi:
        conditions.append(func.lower(Work.doi) == normalize_doi(reference.doi))
    base = _arxiv_base_of(arxiv_id=reference.arxiv_id, doi=reference.doi)
    if base:
        conditions.append(Work.arxiv_base_id == base)
        # Bridge the other spelling too: a work whose only identifier is the arXiv DOI.
        conditions.append(func.lower(Work.doi) == f"10.48550/arxiv.{base}")
    if not conditions:
        return []
    return list(db.scalars(select(Work).where(or_(*conditions))).all())


# Leading function words that citation styles add/drop freely ("The mathematical theory of…" vs
# "Mathematical theory of…"). Blocking must not let such a variant land in a different block, or
# the pair is never even fuzzy-compared.
_LEADING_STOPWORDS = ("a", "an", "the", "on", "of", "in", "for", "to", "toward", "towards")


def _relaxed_blocking_key(normalized_title: str) -> str:
    """First *content* token of a normalized title: leading stopwords are skipped.

    Falls back to the plain first token when the whole title is stopwords.
    """
    tokens = normalized_title.split()
    for token in tokens:
        if token not in _LEADING_STOPWORDS:
            return token
    return tokens[0] if tokens else ""


def _block_conditions(column, normalized_title: str) -> list:
    """SQL conditions matching titles in ``normalized_title``'s block, stopword-tolerantly.

    The stored ``normalized_title`` column keeps leading stopwords, so the block query must accept
    both the bare block ("mathematical theory …") and every "<stopword> block …" variant — a
    bounded OR list, cheap at library scale.
    """
    block = _relaxed_blocking_key(normalized_title)
    if not block:
        return []
    escaped = block.replace("%", r"\%")
    conditions = [column == block, column.like(escaped + " %")]
    for stopword in _LEADING_STOPWORDS:
        conditions.append(column == f"{stopword} {block}")
        conditions.append(column.like(f"{stopword} {escaped} %"))
    return conditions


def _blocking_candidates(db: Session, normalized_title: str) -> list[Work]:
    conditions = _block_conditions(Work.normalized_title, normalized_title)
    if not conditions:
        return []
    return list(db.scalars(select(Work).where(or_(*conditions))).all())


def find_reference_match(
    db: Session,
    reference: Matchable,
    *,
    settings: Settings | None = None,
    candidate_works: Sequence[Work] | None = None,
    author_names: Mapping[uuid.UUID, list[str]] | None = None,
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
        score = title_similarity_pct(ref_title, work.canonical_title)
        if score < settings.reference_matching_title_threshold:
            continue
        if not _year_ok(reference, work, settings):
            continue
        if not _author_ok(db, reference, work, settings, author_names=author_names):
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
    author_names: Mapping[uuid.UUID, list[str]] | None = None,
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

    match = find_reference_match(
        db, reference, settings=settings, candidate_works=candidate_works, author_names=author_names
    )

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


@dataclass
class MatchIndexes:
    """In-memory candidate indexes over the whole library, for the full rescan job (S8/S9).

    Holds every non-shadow Work once (hard assumption: the library fits in RAM — a few MB per
    10k works) plus the two candidate-selection keys the SQL path uses (identifier keys and the
    stopword-relaxed title block) and a prebuilt work→author-names map, so a full-library rescan
    does O(rows) dict lookups instead of 2-3 SQL point queries per reference.
    """

    identifier: dict[str, list[Work]]
    blocks: dict[str, list[Work]]
    author_names: dict[uuid.UUID, list[str]]


def build_match_indexes(db: Session) -> MatchIndexes:
    """Load all candidate works + author names once and index them for in-memory matching."""
    identifier: dict[str, list[Work]] = {}
    blocks: dict[str, list[Work]] = {}
    for work in db.scalars(select(Work).where(Work.merged_into_id.is_(None))).all():
        for key in _identifier_keys(doi=work.doi, arxiv_id=work.arxiv_id):
            identifier.setdefault(key, []).append(work)
        base = _work_arxiv_base(work)
        if base:
            identifier.setdefault(f"arxiv:{base}", []).append(work)
        if work.normalized_title:
            block = _relaxed_blocking_key(work.normalized_title)
            if block:
                blocks.setdefault(block, []).append(work)
    # Best authors assertion per work, one query (canonical first, then confidence) — first wins.
    author_names: dict[uuid.UUID, list[str]] = {}
    rows = db.execute(
        select(MetadataAssertion.entity_id, MetadataAssertion.value)
        .where(
            MetadataAssertion.entity_type == "work",
            MetadataAssertion.field_name == "authors",
        )
        .order_by(
            MetadataAssertion.selected_as_canonical.desc(),
            func.coalesce(MetadataAssertion.confidence, 0).desc(),
        )
    ).all()
    for work_id, value in rows:
        if work_id not in author_names:
            author_names[work_id] = [n.strip() for n in (value or "").split(";") if n.strip()]
    return MatchIndexes(identifier=identifier, blocks=blocks, author_names=author_names)


def candidates_from_indexes(
    indexes: MatchIndexes,
    *,
    doi: str | None,
    arxiv_id: str | None,
    normalized_title: str | None,
) -> list[Work]:
    """The plausible candidate works for one reference-shaped row, deduplicated, from the indexes.

    Mirrors the SQL candidate selection (identifier lookups + stopword-relaxed title block), so
    matching over these candidates decides exactly what the per-row queries would.
    """
    seen: dict[uuid.UUID, Work] = {}
    for key in _identifier_keys(doi=doi, arxiv_id=arxiv_id):
        for work in indexes.identifier.get(key, ()):
            seen.setdefault(work.id, work)
    if normalized_title:
        block = _relaxed_blocking_key(normalized_title)
        if block:
            for work in indexes.blocks.get(block, ()):
                seen.setdefault(work.id, work)
    return list(seen.values())


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
    base = _work_arxiv_base(work) or (arxiv_base_from_doi(work.doi) if work.doi else None)
    if base:
        # Stored reference arXiv ids keep their raw decorations ("arXiv:…vN"), so match by
        # containment; references carrying the arXiv-DOI spelling are matched exactly.
        conditions.append(Reference.arxiv_id.ilike(f"%{base}%"))
        conditions.append(Reference.doi == f"10.48550/arxiv.{base}")
    if work.normalized_title:
        conditions.extend(_block_conditions(Reference.normalized_title, work.normalized_title))
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
