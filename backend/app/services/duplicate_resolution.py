"""Apply reviewed duplicate/version candidate decisions."""

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.annotation import Annotation
from app.models.citation import CitationMention, Reference, ReferenceCitation
from app.models.duplicate import DuplicateCandidate
from app.models.file import File, FileWorkLink
from app.models.metadata import MetadataAssertion
from app.models.organization import ShelfWork, TagLink
from app.models.user import User
from app.models.work import Work, WorkLink, WorkVersion
from app.services.audit import record_event
from app.services.default_shelf import place_on_default_if_loose
from app.services.duplicate_detection import split_arxiv_id
from app.utils.normalization import normalize_doi, normalize_title

# Provenance source stamped on the metadata assertions a merge synthesises (the base's kept value
# and the source's conflicting value), so the metadata-review UI shows them as a resolvable conflict.
_MERGE_SOURCE = "merge"
# Non-identifier Work columns consolidated field-by-field on merge (empty base field filled;
# differing values kept as a conflict assertion). The unique-indexed identifiers ``doi`` /
# ``arxiv_*`` are handled separately by ``_transfer_identifiers`` (a shadow must not keep a value
# that duplicates the base's, which would violate the ``uq_works_doi`` / ``uq_works_arxiv_base_id``
# unique index on Postgres).
_MERGE_FIELDS = ("title", "abstract", "year", "venue")

DuplicateAction = Literal[
    "merge_works",
    "link_as_version",
    "mark_duplicate_file",
    "split_file",
    "keep_separate",
    "ignore",
]


def apply_duplicate_action(
    db: Session,
    *,
    candidate: DuplicateCandidate,
    action: DuplicateAction,
    actor: User,
    target_work_id: uuid.UUID | None = None,
) -> DuplicateCandidate:
    """Apply a review decision without deleting works or files."""
    if candidate.status != "open":
        raise ValueError(
            "Candidate has already been resolved; reopen it before applying another action"
        )
    if action == "ignore":
        # Transient dismissal (owner decision): "ignore for now" adds NO permanent flag — delete the
        # candidate so it drops from the current results but a future scan re-surfaces it. Contrast
        # "keep_separate", which persists a reviewable/revocable "rejected" flag. Audit first.
        record_event(
            db,
            "duplicate_candidate.resolved",
            actor_user_id=actor.id,
            entity_type="duplicate_candidate",
            entity_id=str(candidate.id),
            details={
                "action": "ignore",
                "status": "dismissed",
                "candidate_type": candidate.candidate_type,
            },
        )
        db.delete(candidate)
        return candidate

    if action == "merge_works":
        _merge_work_candidate(db, candidate, target_work_id=target_work_id, actor=actor)
        _resolve(candidate, actor, "accepted", action)
    elif action == "link_as_version":
        _link_work_candidate_as_version(db, candidate, target_work_id=target_work_id)
        _resolve(candidate, actor, "accepted", action)
    elif action == "mark_duplicate_file":
        _mark_duplicate_file_candidate(db, candidate)
        _resolve(candidate, actor, "accepted", action)
    elif action == "split_file":
        raise ValueError("split_file requires explicit split segments")
    elif action == "keep_separate":
        _resolve(candidate, actor, "rejected", action)
    else:
        raise ValueError(f"Unsupported duplicate action: {action}")

    record_event(
        db,
        "duplicate_candidate.resolved",
        actor_user_id=actor.id,
        entity_type="duplicate_candidate",
        entity_id=str(candidate.id),
        details={
            "action": action,
            "status": candidate.status,
            "candidate_type": candidate.candidate_type,
            "target_work_id": str(target_work_id) if target_work_id else None,
        },
    )
    return candidate


def split_multiwork_file(
    db: Session,
    *,
    candidate: DuplicateCandidate,
    actor: User,
    segments: list[dict[str, Any]],
) -> DuplicateCandidate:
    """Create work/file-segment links for a reviewed multi-paper file."""
    if candidate.candidate_type != "multiwork_file" or candidate.entity_a_type != "file":
        raise ValueError("split_file requires a multiwork file candidate")
    if not segments:
        raise ValueError("split_file requires at least one segment")
    if candidate.status != "open":
        raise ValueError(
            "Candidate has already been resolved; reopen it before applying another action"
        )
    # Reopening does not undo a prior split, so refuse to split the same file twice and
    # create a second set of duplicate works.
    if (candidate.signals or {}).get("created_work_ids"):
        raise ValueError("This file has already been split; remove the created works to redo it")

    from app.models.file import FileSegment
    from app.utils.normalization import normalize_title

    file_id = candidate.entity_a_id
    created_work_ids: list[str] = []
    for index, segment_payload in enumerate(segments, start=1):
        label = str(segment_payload.get("label") or segment_payload.get("title") or "").strip()
        title = str(segment_payload.get("title") or label or f"Segment {index}").strip()
        segment = FileSegment(
            file_id=file_id,
            page_start=segment_payload.get("page_start"),
            page_end=segment_payload.get("page_end"),
            label=label or title,
            segment_type="paper",
            created_by="user",
            confidence=100,
        )
        work = Work(
            canonical_title=title,
            normalized_title=normalize_title(title),
            canonical_metadata_source="multiwork_split",
            user_confirmed=True,
        )
        db.add_all([segment, work])
        db.flush()
        db.add(
            FileWorkLink(
                file_id=file_id,
                work_id=work.id,
                segment_id=segment.id,
                relationship_type="contains",
                warning_state="file_contains_multiple_works",
                user_confirmed=True,
            )
        )
        created_work_ids.append(str(work.id))

    _resolve(candidate, actor, "accepted", "split_file")
    candidate.signals = {
        **(candidate.signals or {}),
        "split_segment_count": len(segments),
        "created_work_ids": created_work_ids,
    }
    record_event(
        db,
        "duplicate_candidate.resolved",
        actor_user_id=actor.id,
        entity_type="duplicate_candidate",
        entity_id=str(candidate.id),
        details={
            "action": "split_file",
            "status": candidate.status,
            "candidate_type": candidate.candidate_type,
            "segment_count": len(segments),
        },
    )
    return candidate


def _merge_work_candidate(
    db: Session,
    candidate: DuplicateCandidate,
    *,
    target_work_id: uuid.UUID | None,
    actor: User,
) -> None:
    """Consolidate the source work into the base and hide the source as a reversible shadow."""
    base, source = _work_pair(db, candidate, target_work_id=target_work_id)
    merge_works(db, base=base, source=source, actor=actor)


def merge_works(db: Session, *, base: Work, source: Work, actor: User) -> Work:
    """Merge ``source`` INTO ``base`` (the surviving canonical paper) in one transaction.

    Fills the base's empty fields from the source (keeping provenance) and, where both hold a
    differing value, records the source's value as a per-field metadata *conflict* on the base
    (never silently overwriting, never touching a locked base field). Moves every owned entity
    (file links, shelf/rack memberships, tags, outgoing references + mentions, annotations,
    versions, non-field metadata assertions) onto the base, and redirects the source's INCOMING
    references to the base. The source becomes a hidden shadow (``merged_into_id`` set) carrying a
    ``merge_record`` that :func:`unmerge_work` uses to reverse exactly this merge.

    Flatten-on-re-merge: if the base already has a reversible shadow, that prior merge is finalized
    first (kept hidden + redirected, but made permanent) so only the newest merge stays reversible.
    """
    if base.id == source.id:
        raise ValueError("Cannot merge a paper into itself")
    if base.merged_into_id is not None:
        raise ValueError("Cannot merge into a paper that is itself a merged shadow")
    if source.merged_into_id is not None:
        raise ValueError("Cannot merge a paper that is already a merged shadow")

    _finalize_reversible_shadows(db, base)

    now = datetime.now(UTC)
    record: dict[str, Any] = {
        "base_id": str(base.id),
        "merged_at": now.isoformat(),
        "filled_fields": [],
        "added_assertion_ids": [],
        "moved": {
            "file_work_links": [],
            "shelf_works": [],
            "tag_links": [],
            "reference_links_out": [],
            "reference_links_dropped": [],
            "references_in": [],
            "mentions_out": [],
            "mentions_in": [],
            "annotations": [],
            "work_versions": [],
            "metadata_assertions": [],
        },
        "transferred": {},
        "base_prior_metadata_source": base.canonical_metadata_source,
        "base_main_file_was_null": base.main_file_id is None,
        "base_queue_position_was_null": base.queue_position is None,
        "shadow_prior_work_type": source.work_type,
        "shadow_prior_metadata_source": source.canonical_metadata_source,
    }

    _fill_and_conflict_fields(db, base=base, source=source, record=record)
    _transfer_identifiers(db, base=base, source=source, record=record)
    _move_owned_entities(db, base=base, source=source, record=record)
    ref_ids, mention_ids = redirect_references(db, source_id=source.id, base_id=base.id)
    record["moved"]["references_in"] = ref_ids
    record["moved"]["mentions_in"] = mention_ids

    if base.main_file_id is None and source.main_file_id is not None:
        base.main_file_id = source.main_file_id
    if base.queue_position is None and source.queue_position is not None:
        base.queue_position = source.queue_position

    source.merged_into_id = base.id
    source.merge_record = record
    source.work_type = "merged"
    source.canonical_metadata_source = "merged"
    source.updated_at = now
    base.updated_at = now
    db.flush()
    place_on_default_if_loose(db, base.id, actor_id=actor.id)  # no free-floating papers (#1)
    return base


def redirect_references(
    db: Session, *, source_id: uuid.UUID, base_id: uuid.UUID
) -> tuple[list[str], list[str]]:
    """Repoint every reference/mention that resolved to ``source_id`` so it now resolves to the base.

    The shared "link-fixing" primitive: other works that cite the merged-away paper keep citing the
    surviving base instead. Returns the ids of the redirected references and citation mentions so a
    caller (merge) can record them for an exact reversal.
    """
    ref_ids: list[str] = []
    for ref in db.scalars(select(Reference).where(Reference.resolved_work_id == source_id)).all():
        ref.resolved_work_id = base_id
        ref_ids.append(str(ref.id))
    mention_ids: list[str] = []
    for mention in db.scalars(
        select(CitationMention).where(CitationMention.resolved_cited_work_id == source_id)
    ).all():
        mention.resolved_cited_work_id = base_id
        mention_ids.append(str(mention.id))
    return ref_ids, mention_ids


def _finalize_reversible_shadows(db: Session, base: Work) -> None:
    """Make any existing reversible shadow of ``base`` permanent (flatten-on-re-merge).

    The shadow stays hidden and its references stay redirected to the base; dropping its
    ``merge_record`` just means it can no longer be unmerged. Keeps unmerge single-level.
    """
    for shadow in db.scalars(
        select(Work).where(Work.merged_into_id == base.id, Work.merge_record.is_not(None))
    ).all():
        shadow.merge_record = None
        shadow.updated_at = datetime.now(UTC)


def _link_work_candidate_as_version(
    db: Session,
    candidate: DuplicateCandidate,
    *,
    target_work_id: uuid.UUID | None,
) -> None:
    """Record a bidirectional "related / same work" link — no file move, no hiding, no deletion."""
    base, source = _work_pair(db, candidate, target_work_id=target_work_id)
    link_works(db, base.id, source.id)


def link_works(
    db: Session,
    work_a_id: uuid.UUID,
    work_b_id: uuid.UUID,
    *,
    link_type: str = "related",
    actor_id: uuid.UUID | None = None,
) -> WorkLink:
    """Create (idempotently) a bidirectional related-works link between two distinct papers.

    Both papers and their own files are kept; the pair is stored order-normalized so the same
    relationship is never duplicated regardless of which side the user picked as base.
    """
    if work_a_id == work_b_id:
        raise ValueError("Cannot link a paper to itself")
    work_a = db.get(Work, work_a_id)
    work_b = db.get(Work, work_b_id)
    if work_a is None or work_b is None:
        raise ValueError("Link references a missing paper")
    if work_a.merged_into_id is not None or work_b.merged_into_id is not None:
        raise ValueError("Cannot link a merged shadow paper")
    lo, hi = sorted((work_a_id, work_b_id), key=str)
    existing = db.scalar(
        select(WorkLink).where(
            WorkLink.work_a_id == lo,
            WorkLink.work_b_id == hi,
            WorkLink.link_type == link_type,
        )
    )
    if existing is not None:
        return existing
    link = WorkLink(work_a_id=lo, work_b_id=hi, link_type=link_type, created_by_user_id=actor_id)
    db.add(link)
    db.flush()
    return link


def linked_work_ids(db: Session, work_id: uuid.UUID) -> list[uuid.UUID]:
    """Return the ids of the papers bidirectionally linked to ``work_id`` (either side of a link)."""
    ids: list[uuid.UUID] = []
    for a, b in db.execute(
        select(WorkLink.work_a_id, WorkLink.work_b_id).where(
            (WorkLink.work_a_id == work_id) | (WorkLink.work_b_id == work_id)
        )
    ).all():
        ids.append(b if a == work_id else a)
    return ids


def unmerge_work(db: Session, *, base_id: uuid.UUID, actor: User) -> Work:
    """Reverse the most recent (reversible) merge into ``base_id`` in one transaction.

    Restores the shadow to a standalone visible paper: moves every recorded entity back, un-redirects
    the incoming references, nulls the base fields the merge filled, and removes the conflict
    assertions the merge added — leaving the two separate papers exactly as before that merge.
    """
    shadow = db.scalar(
        select(Work).where(Work.merged_into_id == base_id, Work.merge_record.is_not(None))
    )
    if shadow is None:
        raise ValueError("This paper has no reversible merge to undo")
    base = db.get(Work, base_id)
    if base is None:
        raise ValueError("The base paper no longer exists")
    record = shadow.merge_record or {}
    moved = record.get("moved", {})

    _move_ids(db, FileWorkLink, moved.get("file_work_links", []), "work_id", shadow.id)
    _move_shelf_works_back(db, moved.get("shelf_works", []), base_id=base_id, shadow_id=shadow.id)
    _move_tag_links_back(db, moved.get("tag_links", []), base_id=base_id, shadow_id=shadow.id)
    # Outgoing citation edges: repoint the moved link rows back, and recreate the ones dropped as
    # duplicates during the merge (they belonged to the shadow originally).
    _move_ids(
        db, ReferenceCitation, moved.get("reference_links_out", []), "citing_work_id", shadow.id
    )
    for ref_id in moved.get("reference_links_dropped", []):
        db.add(ReferenceCitation(reference_id=uuid.UUID(ref_id), citing_work_id=shadow.id))
    _move_ids(db, Reference, moved.get("references_in", []), "resolved_work_id", shadow.id)
    _move_ids(db, CitationMention, moved.get("mentions_out", []), "citing_work_id", shadow.id)
    _move_ids(
        db, CitationMention, moved.get("mentions_in", []), "resolved_cited_work_id", shadow.id
    )
    _move_ids(db, Annotation, moved.get("annotations", []), "work_id", shadow.id)
    _move_ids(db, WorkVersion, moved.get("work_versions", []), "work_id", shadow.id)
    _move_ids(db, MetadataAssertion, moved.get("metadata_assertions", []), "entity_id", shadow.id)

    for assertion_id in record.get("added_assertion_ids", []):
        assertion = db.get(MetadataAssertion, uuid.UUID(assertion_id))
        if assertion is not None:
            db.delete(assertion)
    for field in record.get("filled_fields", []):
        _clear_field(base, field)
    base.canonical_metadata_source = record.get("base_prior_metadata_source")
    if record.get("base_main_file_was_null"):
        base.main_file_id = None
    if record.get("base_queue_position_was_null"):
        base.queue_position = None

    # Hand the unique-indexed identifiers back to the shadow. Release them on the base and flush
    # FIRST so the base no longer holds the value when the shadow reclaims it (the uq_works_doi /
    # uq_works_arxiv_base_id index is checked per statement, not deferred).
    transferred = record.get("transferred", {})
    if "doi" in transferred:
        base.doi = None
    if "arxiv_base_id" in transferred:
        base.arxiv_id = None
        base.arxiv_base_id = None
    if transferred:
        db.flush()
    if "doi" in transferred:
        shadow.doi = transferred["doi"]
    if "arxiv_base_id" in transferred:
        shadow.arxiv_id = transferred["arxiv_id"]
        shadow.arxiv_base_id = transferred["arxiv_base_id"]

    shadow.merged_into_id = None
    shadow.merge_record = None
    shadow.work_type = record.get("shadow_prior_work_type") or "unknown"
    shadow.canonical_metadata_source = record.get("shadow_prior_metadata_source")
    now = datetime.now(UTC)
    shadow.updated_at = now
    base.updated_at = now
    db.flush()
    record_event(
        db,
        "duplicate_merge.unmerged",
        actor_user_id=actor.id,
        entity_type="work",
        entity_id=str(base.id),
        details={"shadow_id": str(shadow.id)},
    )
    return shadow


def merge_preview(db: Session, *, base: Work, source: Work) -> dict[str, Any]:
    """Read-only summary of what merging ``source`` into ``base`` would do (for the confirm UI)."""
    fill_fields: list[str] = []
    conflict_fields: list[str] = []
    locked = set(base.confirmed_fields or [])
    for field in _MERGE_FIELDS:
        src_val = _get_field(source, field)
        if _is_empty(src_val):
            continue
        base_val = _get_field(base, field)
        if _is_empty(base_val):
            if field not in locked and not base.user_confirmed:
                fill_fields.append(field)
        elif _field_values_differ(field, base_val, src_val):
            conflict_fields.append(field)
    # doi / arXiv are transferred separately (unique-indexed), not via ``_MERGE_FIELDS``.
    if source.doi:
        if not base.doi and "doi" not in locked and not base.user_confirmed:
            fill_fields.append("doi")
        elif base.doi and normalize_doi(base.doi) != normalize_doi(source.doi):
            conflict_fields.append("doi")
    if source.arxiv_base_id and not base.arxiv_base_id:
        fill_fields.append("arxiv")
    base_files = set(
        db.scalars(select(FileWorkLink.file_id).where(FileWorkLink.work_id == base.id)).all()
    )
    file_count = sum(
        1
        for file_id in db.scalars(
            select(FileWorkLink.file_id).where(FileWorkLink.work_id == source.id)
        ).all()
        if file_id not in base_files
    )
    incoming = len(
        db.scalars(select(Reference.id).where(Reference.resolved_work_id == source.id)).all()
    )
    return {
        "base_work_id": base.id,
        "source_work_id": source.id,
        "fill_fields": fill_fields,
        "conflict_fields": conflict_fields,
        "file_count": file_count,
        "incoming_reference_count": incoming,
        "will_flatten": has_reversible_shadow(db, base.id),
    }


def has_reversible_shadow(db: Session, work_id: uuid.UUID) -> bool:
    """True if ``work_id`` is a base with a reversible (unmergeable) shadow."""
    return (
        db.scalar(
            select(Work.id).where(Work.merged_into_id == work_id, Work.merge_record.is_not(None))
        )
        is not None
    )


def _mark_duplicate_file_candidate(db: Session, candidate: DuplicateCandidate) -> None:
    if {candidate.entity_a_type, candidate.entity_b_type} != {"file"}:
        raise ValueError("mark_duplicate_file requires a file/file candidate")
    duplicate_file_id = candidate.entity_b_id
    for link in db.scalars(select(FileWorkLink).where(FileWorkLink.file_id == duplicate_file_id)):
        link.relationship_type = "duplicate_copy"
        link.warning_state = "work_has_multiple_files"
        link.user_confirmed = True


def _work_pair(
    db: Session,
    candidate: DuplicateCandidate,
    *,
    target_work_id: uuid.UUID | None,
) -> tuple[Work, Work]:
    if candidate.entity_a_type != "work" or candidate.entity_b_type != "work":
        raise ValueError("Action requires a work/work candidate")
    work_a = db.get(Work, candidate.entity_a_id)
    work_b = db.get(Work, candidate.entity_b_id)
    if work_a is None or work_b is None:
        raise ValueError("Candidate references a missing work")
    if target_work_id is None:
        target = choose_target_work(work_a, work_b)
        return (target, work_b) if target is work_a else (target, work_a)
    if target_work_id == work_a.id:
        return work_a, work_b
    if target_work_id == work_b.id:
        return work_b, work_a
    raise ValueError("target_work_id must be one of the candidate works")


def choose_target_work(work_a: Work, work_b: Work) -> Work:
    """Pick the work that should survive as canonical when the user gives no explicit target.

    Prefers a user-confirmed work, then the later arXiv version (version collapse keeps the
    newest as canonical), then the more complete metadata record. Ties keep ``work_a`` so the
    choice is stable.
    """
    return work_a if _target_rank(work_a) >= _target_rank(work_b) else work_b


def _target_rank(work: Work) -> tuple[int, int, int]:
    confirmed = 1 if work.user_confirmed else 0
    version = split_arxiv_id(work.arxiv_id)["version"]
    version_number = int(version[1:]) if version else 0
    completeness = sum(
        1
        for value in (work.doi, work.abstract, work.venue, work.year, work.canonical_title)
        if value
    )
    return (confirmed, version_number, completeness)


def _get_field(work: Work, field: str) -> Any:
    return {
        "title": work.canonical_title,
        "abstract": work.abstract,
        "year": work.year,
        "venue": work.venue,
        "doi": work.doi,
    }[field]


def _set_field(work: Work, field: str, value: Any, *, source: str) -> None:
    if field == "title":
        work.canonical_title = value
        work.normalized_title = normalize_title(str(value))
        work.canonical_metadata_source = source
    elif field == "abstract":
        work.abstract = value
    elif field == "year":
        work.year = value
    elif field == "venue":
        work.venue = value
    elif field == "doi":
        work.doi = value


def _clear_field(work: Work, field: str) -> None:
    if field == "title":
        work.canonical_title = None
        work.normalized_title = None
    elif field == "abstract":
        work.abstract = None
    elif field == "year":
        work.year = None
    elif field == "venue":
        work.venue = None
    elif field == "doi":
        work.doi = None


def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _field_values_differ(field: str, base_val: Any, src_val: Any) -> bool:
    if field == "title":
        return normalize_title(str(base_val)) != normalize_title(str(src_val))
    if field == "doi":
        return normalize_doi(str(base_val)) != normalize_doi(str(src_val))
    return str(base_val).strip() != str(src_val).strip()


def _add_merge_assertion(
    db: Session, base: Work, field: str, value: Any, *, canonical: bool
) -> uuid.UUID:
    assertion = MetadataAssertion(
        id=uuid.uuid4(),
        entity_type="work",
        entity_id=base.id,
        field_name=field,
        value=str(value),
        source=_MERGE_SOURCE,
        selected_as_canonical=canonical,
    )
    db.add(assertion)
    return assertion.id


def _fill_and_conflict_fields(
    db: Session, *, base: Work, source: Work, record: dict[str, Any]
) -> None:
    """Fill the base's empty fields from the source; flag differing values as conflicts.

    A locked base field (``confirmed_fields`` / ``user_confirmed``) is never overwritten; the
    source's value is still surfaced as a conflict assertion so nothing is silently dropped.
    """
    locked = set(base.confirmed_fields or [])
    for field in _MERGE_FIELDS:
        src_val = _get_field(source, field)
        if _is_empty(src_val):
            continue
        base_val = _get_field(base, field)
        field_locked = field in locked or base.user_confirmed
        if _is_empty(base_val):
            if field_locked:
                aid = _add_merge_assertion(db, base, field, src_val, canonical=False)
                record["added_assertion_ids"].append(str(aid))
                continue
            _set_field(base, field, src_val, source=_MERGE_SOURCE)
            record["filled_fields"].append(field)
            aid = _add_merge_assertion(db, base, field, src_val, canonical=True)
            record["added_assertion_ids"].append(str(aid))
        elif _field_values_differ(field, base_val, src_val):
            # Both hold a differing value → a real conflict the user resolves later. Ensure the
            # base's kept value is represented as an assertion (so the review UI shows ≥2 distinct
            # values), then add the source's value as a non-canonical conflicting assertion.
            has_assertion = db.scalar(
                select(MetadataAssertion.id)
                .where(
                    MetadataAssertion.entity_type == "work",
                    MetadataAssertion.entity_id == base.id,
                    MetadataAssertion.field_name == field,
                )
                .limit(1)
            )
            if has_assertion is None:
                aid = _add_merge_assertion(db, base, field, base_val, canonical=True)
                record["added_assertion_ids"].append(str(aid))
            aid = _add_merge_assertion(db, base, field, src_val, canonical=False)
            record["added_assertion_ids"].append(str(aid))


def _transfer_identifiers(db: Session, *, base: Work, source: Work, record: dict[str, Any]) -> None:
    """Consolidate the unique-indexed identifiers (doi / arXiv) from the source into the base.

    Because ``doi`` and ``arxiv_base_id`` carry a unique index, the shadow must not keep a value the
    base now holds. When the base's identifier is empty it is *moved* from the source (base set,
    source cleared) — recorded so unmerge can hand it back. When both hold a differing DOI it stays a
    metadata conflict on the base (no column change, no collision).
    """
    locked = set(base.confirmed_fields or [])
    transferred = record["transferred"]
    if source.doi:
        if not base.doi and "doi" not in locked and not base.user_confirmed:
            # Release on the source and flush FIRST so the base can reclaim the unique value without
            # tripping uq_works_doi (both rows momentarily holding it otherwise).
            value = source.doi
            source.doi = None
            db.flush()
            base.doi = value
            transferred["doi"] = value
            aid = _add_merge_assertion(db, base, "doi", value, canonical=True)
            record["added_assertion_ids"].append(str(aid))
        elif base.doi and normalize_doi(base.doi) != normalize_doi(source.doi):
            has_assertion = db.scalar(
                select(MetadataAssertion.id)
                .where(
                    MetadataAssertion.entity_type == "work",
                    MetadataAssertion.entity_id == base.id,
                    MetadataAssertion.field_name == "doi",
                )
                .limit(1)
            )
            if has_assertion is None:
                record["added_assertion_ids"].append(
                    str(_add_merge_assertion(db, base, "doi", base.doi, canonical=True))
                )
            record["added_assertion_ids"].append(
                str(_add_merge_assertion(db, base, "doi", source.doi, canonical=False))
            )
    if source.arxiv_base_id and not base.arxiv_base_id:
        transferred["arxiv_id"] = source.arxiv_id
        transferred["arxiv_base_id"] = source.arxiv_base_id
        new_arxiv_id = base.arxiv_id or source.arxiv_id
        new_arxiv_base_id = source.arxiv_base_id
        source.arxiv_id = None
        source.arxiv_base_id = None
        db.flush()
        base.arxiv_id = new_arxiv_id
        base.arxiv_base_id = new_arxiv_base_id


def _move_owned_entities(db: Session, *, base: Work, source: Work, record: dict[str, Any]) -> None:
    moved = record["moved"]

    # File links: repoint to the base, unless the base already links that same file (then leave the
    # source link in place so nothing is destroyed and unmerge stays exact).
    base_files = set(
        db.scalars(select(FileWorkLink.file_id).where(FileWorkLink.work_id == base.id)).all()
    )
    for link in db.scalars(select(FileWorkLink).where(FileWorkLink.work_id == source.id)).all():
        if link.file_id in base_files:
            continue
        link.work_id = base.id
        moved["file_work_links"].append(str(link.id))

    # Shelf memberships (PK shelf_id, work_id): repoint unless the base is already on that shelf.
    base_shelves = set(
        db.scalars(select(ShelfWork.shelf_id).where(ShelfWork.work_id == base.id)).all()
    )
    for membership in db.scalars(select(ShelfWork).where(ShelfWork.work_id == source.id)).all():
        if membership.shelf_id in base_shelves:
            continue
        membership.work_id = base.id
        moved["shelf_works"].append(str(membership.shelf_id))

    # Tags (PK tag_id, entity_type, entity_id): repoint unless the base already carries that tag.
    base_tags = set(
        db.scalars(
            select(TagLink.tag_id).where(
                TagLink.entity_type == "work", TagLink.entity_id == base.id
            )
        ).all()
    )
    for tag_link in db.scalars(
        select(TagLink).where(TagLink.entity_type == "work", TagLink.entity_id == source.id)
    ).all():
        if tag_link.tag_id in base_tags:
            continue
        tag_link.entity_id = base.id
        moved["tag_links"].append(str(tag_link.tag_id))

    # Outgoing citation edges (source as the CITING work): repoint the link rows to base, dropping
    # any that would duplicate a link base already has onto the same shared canonical reference.
    base_cited_ref_ids = set(
        db.scalars(
            select(ReferenceCitation.reference_id).where(
                ReferenceCitation.citing_work_id == base.id
            )
        ).all()
    )
    for link in db.scalars(
        select(ReferenceCitation).where(ReferenceCitation.citing_work_id == source.id)
    ).all():
        if link.reference_id in base_cited_ref_ids:
            moved["reference_links_dropped"].append(str(link.reference_id))
            db.delete(link)
        else:
            link.citing_work_id = base.id
            base_cited_ref_ids.add(link.reference_id)
            moved["reference_links_out"].append(str(link.id))
    for mention in db.scalars(
        select(CitationMention).where(CitationMention.citing_work_id == source.id)
    ).all():
        mention.citing_work_id = base.id
        moved["mentions_out"].append(str(mention.id))

    # Annotations + versions.
    for annotation in db.scalars(select(Annotation).where(Annotation.work_id == source.id)).all():
        annotation.work_id = base.id
        moved["annotations"].append(str(annotation.id))
    for version in db.scalars(select(WorkVersion).where(WorkVersion.work_id == source.id)).all():
        version.work_id = base.id
        moved["work_versions"].append(str(version.id))

    # Non-field metadata assertions (e.g. authors): move to the base, deduping identical values.
    # The consolidated fields (title/abstract/…) are handled by ``_fill_and_conflict_fields`` and
    # deliberately left on the source as its own provenance.
    base_field_values = {
        (field_name, value)
        for field_name, value in db.execute(
            select(MetadataAssertion.field_name, MetadataAssertion.value).where(
                MetadataAssertion.entity_type == "work",
                MetadataAssertion.entity_id == base.id,
            )
        ).all()
    }
    for assertion in db.scalars(
        select(MetadataAssertion).where(
            MetadataAssertion.entity_type == "work",
            MetadataAssertion.entity_id == source.id,
            MetadataAssertion.field_name.notin_(_MERGE_FIELDS),
        )
    ).all():
        if (assertion.field_name, assertion.value) in base_field_values:
            continue
        assertion.entity_id = base.id
        moved["metadata_assertions"].append(str(assertion.id))


def _move_ids(db: Session, model: type, ids: list[str], attr: str, new_value: uuid.UUID) -> None:
    """Set ``attr = new_value`` on each ``model`` row whose id is in ``ids`` (unmerge reversal)."""
    for row_id in ids:
        row = db.get(model, uuid.UUID(row_id))
        if row is not None:
            setattr(row, attr, new_value)


def _move_shelf_works_back(
    db: Session, shelf_ids: list[str], *, base_id: uuid.UUID, shadow_id: uuid.UUID
) -> None:
    for shelf_id in shelf_ids:
        membership = db.get(ShelfWork, {"shelf_id": uuid.UUID(shelf_id), "work_id": base_id})
        if membership is not None:
            membership.work_id = shadow_id


def _move_tag_links_back(
    db: Session, tag_ids: list[str], *, base_id: uuid.UUID, shadow_id: uuid.UUID
) -> None:
    for tag_id in tag_ids:
        tag_link = db.get(
            TagLink,
            {"tag_id": uuid.UUID(tag_id), "entity_type": "work", "entity_id": base_id},
        )
        if tag_link is not None:
            tag_link.entity_id = shadow_id


def _resolve(
    candidate: DuplicateCandidate,
    actor: User,
    status: str,
    action: DuplicateAction,
) -> None:
    candidate.status = status
    candidate.resolved_by_user_id = actor.id
    candidate.resolved_at = datetime.now(UTC)
    candidate.signals = {**(candidate.signals or {}), "review_action": action}


_CANDIDATE_TYPE_LABELS = {
    "same_doi": "Same DOI",
    "same_arxiv": "Same arXiv ID",
    "fuzzy_title": "Similar title",
    "exact_file": "Identical file (SHA-256)",
    "text_fingerprint": "Same text fingerprint",
    "multiwork_file": "File may contain multiple papers",
}


def candidate_entity_maps(
    db: Session, candidates: list[DuplicateCandidate]
) -> tuple[dict[uuid.UUID, Work], dict[uuid.UUID, File]]:
    """Batch-load the works/files referenced by ``candidates`` (one IN() query per entity type).

    Pass the maps into :func:`describe_candidate` to avoid per-candidate ``db.get`` round trips
    when serializing a list of candidates.
    """
    work_ids: set[uuid.UUID] = set()
    file_ids: set[uuid.UUID] = set()
    for candidate in candidates:
        for entity_type, entity_id in (
            (candidate.entity_a_type, candidate.entity_a_id),
            (candidate.entity_b_type, candidate.entity_b_id),
        ):
            if entity_type == "work":
                work_ids.add(entity_id)
            elif entity_type == "file":
                file_ids.add(entity_id)
    works: dict[uuid.UUID, Work] = {}
    files: dict[uuid.UUID, File] = {}
    if work_ids:
        works = {w.id: w for w in db.scalars(select(Work).where(Work.id.in_(work_ids)))}
    if file_ids:
        files = {f.id: f for f in db.scalars(select(File).where(File.id.in_(file_ids)))}
    return works, files


def describe_candidate(
    db: Session,
    candidate: DuplicateCandidate,
    *,
    works: dict[uuid.UUID, Work] | None = None,
    files: dict[uuid.UUID, File] | None = None,
) -> dict[str, Any]:
    """Build human-readable labels + a suggested merge target for a candidate.

    Returned as plain data so the API layer can attach it to the response without the ORM
    needing relationships it does not declare. ``works``/``files`` optionally supply pre-loaded
    lookup maps (see :func:`candidate_entity_maps`); without them each entity is fetched via
    ``db.get``.
    """

    def _work(work_id: uuid.UUID) -> Work | None:
        return works.get(work_id) if works is not None else db.get(Work, work_id)

    def _file(file_id: uuid.UUID) -> File | None:
        return files.get(file_id) if files is not None else db.get(File, file_id)

    a_label = _entity_label(candidate.entity_a_type, candidate.entity_a_id, _work, _file)
    b_label = _entity_label(candidate.entity_b_type, candidate.entity_b_id, _work, _file)

    suggested_target_work_id: uuid.UUID | None = None
    if (
        candidate.entity_a_type == "work"
        and candidate.entity_b_type == "work"
        and candidate.entity_a_id != candidate.entity_b_id
    ):
        work_a = _work(candidate.entity_a_id)
        work_b = _work(candidate.entity_b_id)
        if work_a is not None and work_b is not None:
            suggested_target_work_id = choose_target_work(work_a, work_b).id

    return {
        "entity_a_label": a_label,
        "entity_b_label": b_label,
        "suggested_target_work_id": suggested_target_work_id,
        "summary": _summarize(candidate, a_label, b_label),
    }


def _entity_label(entity_type: str, entity_id: uuid.UUID, get_work, get_file) -> str:
    if entity_type == "work":
        work = get_work(entity_id)
        if work is not None:
            return work.canonical_title or f"Untitled work ({str(work.id)[:8]})"
    elif entity_type == "file":
        file = get_file(entity_id)
        if file is not None:
            return file.original_filename or f"file {file.sha256[:12]}"
    return f"{entity_type} {str(entity_id)[:8]}"


def _summarize(candidate: DuplicateCandidate, a_label: str, b_label: str) -> str:
    kind = _CANDIDATE_TYPE_LABELS.get(candidate.candidate_type, candidate.candidate_type)
    signals = candidate.signals or {}
    if candidate.candidate_type == "multiwork_file":
        return f"{kind}: {a_label}"
    if candidate.candidate_type == "fuzzy_title":
        kind = f"{kind} ({round(candidate.score * 100)}%)"
    elif candidate.candidate_type == "same_arxiv" and signals.get("version_mismatch"):
        kind = f"{kind} (version mismatch)"
    return f"{kind}: “{a_label}” ↔ “{b_label}”"


def reopen_duplicate_candidate(candidate: DuplicateCandidate) -> DuplicateCandidate:
    """Reopen a candidate review without attempting to undo prior side effects."""
    candidate.status = "open"
    candidate.resolved_by_user_id = None
    candidate.resolved_at = None
    signals: dict[str, Any] = dict(candidate.signals or {})
    signals.pop("review_action", None)
    candidate.signals = signals
    return candidate
