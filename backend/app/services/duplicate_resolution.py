"""Apply reviewed duplicate/version candidate decisions."""

import uuid
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.duplicate import DuplicateCandidate
from app.models.file import FileWorkLink
from app.models.organization import ShelfWork, TagLink
from app.models.user import User
from app.models.work import Work, WorkVersion
from app.services.audit import record_event
from app.services.duplicate_detection import split_arxiv_id

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
    if action == "merge_works":
        _merge_work_candidate(db, candidate, target_work_id=target_work_id)
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
    elif action == "ignore":
        _resolve(candidate, actor, "ignored", action)
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
) -> None:
    target, source = _work_pair(db, candidate, target_work_id=target_work_id)
    _move_file_links_to_work(db, source_work=source, target_work=target, version=None)
    _move_shelf_memberships(db, source_work=source, target_work=target)
    _move_work_tags(db, source_work=source, target_work=target)
    source.work_type = "merged"
    source.canonical_metadata_source = "merged"
    source.updated_at = datetime.utcnow()
    target.updated_at = datetime.utcnow()


def _link_work_candidate_as_version(
    db: Session,
    candidate: DuplicateCandidate,
    *,
    target_work_id: uuid.UUID | None,
) -> None:
    target, source = _work_pair(db, candidate, target_work_id=target_work_id)
    arxiv = split_arxiv_id(source.arxiv_id)
    version = WorkVersion(
        work_id=target.id,
        version_label=source.canonical_title or source.arxiv_id or "Linked version",
        source="duplicate_review",
        version_type="arxiv" if source.arxiv_id else "unknown",
        arxiv_version=arxiv["version"],
        doi=source.doi,
    )
    db.add(version)
    db.flush()
    _move_file_links_to_work(db, source_work=source, target_work=target, version=version)
    source.work_type = "version"
    source.canonical_metadata_source = "linked_as_version"
    source.updated_at = datetime.utcnow()
    target.updated_at = datetime.utcnow()


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
    if target_work_id is None or target_work_id == work_a.id:
        return work_a, work_b
    if target_work_id == work_b.id:
        return work_b, work_a
    raise ValueError("target_work_id must be one of the candidate works")


def _move_file_links_to_work(
    db: Session,
    *,
    source_work: Work,
    target_work: Work,
    version: WorkVersion | None,
) -> None:
    for link in list(
        db.scalars(select(FileWorkLink).where(FileWorkLink.work_id == source_work.id))
    ):
        existing = db.scalar(
            select(FileWorkLink).where(
                FileWorkLink.file_id == link.file_id,
                FileWorkLink.work_id == target_work.id,
            )
        )
        if existing is not None:
            existing.version_id = existing.version_id or (version.id if version else None)
            existing.warning_state = "work_has_multiple_files"
            existing.user_confirmed = True
            db.delete(link)
            continue
        link.work_id = target_work.id
        link.version_id = version.id if version else link.version_id
        link.warning_state = "work_has_multiple_files"
        link.user_confirmed = True


def _move_shelf_memberships(db: Session, *, source_work: Work, target_work: Work) -> None:
    for membership in list(
        db.scalars(select(ShelfWork).where(ShelfWork.work_id == source_work.id))
    ):
        existing = db.get(ShelfWork, {"shelf_id": membership.shelf_id, "work_id": target_work.id})
        if existing is None:
            membership.work_id = target_work.id
        else:
            db.delete(membership)


def _move_work_tags(db: Session, *, source_work: Work, target_work: Work) -> None:
    for tag_link in list(
        db.scalars(
            select(TagLink).where(
                TagLink.entity_type == "work",
                TagLink.entity_id == source_work.id,
            )
        )
    ):
        existing = db.get(
            TagLink,
            {
                "tag_id": tag_link.tag_id,
                "entity_type": "work",
                "entity_id": target_work.id,
            },
        )
        if existing is None:
            tag_link.entity_id = target_work.id
        else:
            db.delete(tag_link)


def _resolve(
    candidate: DuplicateCandidate,
    actor: User,
    status: str,
    action: DuplicateAction,
) -> None:
    candidate.status = status
    candidate.resolved_by_user_id = actor.id
    candidate.resolved_at = datetime.utcnow()
    candidate.signals = {**(candidate.signals or {}), "review_action": action}


def reopen_duplicate_candidate(candidate: DuplicateCandidate) -> DuplicateCandidate:
    """Reopen a candidate review without attempting to undo prior side effects."""
    candidate.status = "open"
    candidate.resolved_by_user_id = None
    candidate.resolved_at = None
    signals: dict[str, Any] = dict(candidate.signals or {})
    signals.pop("review_action", None)
    candidate.signals = signals
    return candidate
