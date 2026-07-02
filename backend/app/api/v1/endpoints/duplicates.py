"""Duplicate/version review endpoints."""

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_authenticated_user, require_min_role
from app.core.security import Role
from app.db.session import get_db
from app.models.duplicate import DuplicateCandidate
from app.models.file import File, FileWorkLink
from app.models.user import User
from app.models.work import Work
from app.services import access
from app.services.duplicate_detection import scan_duplicate_candidates
from app.services.duplicate_resolution import (
    apply_duplicate_action,
    candidate_entity_maps,
    describe_candidate,
    reopen_duplicate_candidate,
    split_multiwork_file,
)

router = APIRouter()
DB_DEP = Depends(get_db)
EDITOR_DEP = Depends(require_min_role(Role.EDITOR))
AUTH_DEP = Depends(require_authenticated_user)


def _candidate_visible(candidate: DuplicateCandidate, visible: set[uuid.UUID] | None) -> bool:
    """True if both work-entities of a candidate are visible (``None`` = unrestricted)."""
    if visible is None:
        return True
    for entity_type, entity_id in (
        (candidate.entity_a_type, candidate.entity_a_id),
        (candidate.entity_b_type, candidate.entity_b_id),
    ):
        if entity_type == "work" and entity_id not in visible:
            return False
    return True


CandidateStatus = Literal["open", "accepted", "rejected", "ignored"]
DuplicateAction = Literal[
    "merge_works",
    "link_as_version",
    "mark_duplicate_file",
    "split_file",
    "keep_separate",
    "ignore",
]


class DuplicateCandidateRead(BaseModel):
    id: uuid.UUID
    candidate_type: str
    entity_a_type: str
    entity_a_id: uuid.UUID
    entity_b_type: str
    entity_b_id: uuid.UUID
    score: float
    signals: dict[str, Any]
    status: str
    created_at: datetime
    resolved_by_user_id: uuid.UUID | None = None
    resolved_at: datetime | None = None
    # Enrichment (computed; see describe_candidate) — never read off the ORM row.
    entity_a_label: str | None = None
    entity_b_label: str | None = None
    suggested_target_work_id: uuid.UUID | None = None
    summary: str | None = None

    model_config = {"from_attributes": True}


def _candidate_view(
    db: Session,
    candidate: DuplicateCandidate,
    *,
    works: dict[uuid.UUID, Work] | None = None,
    files: dict[uuid.UUID, File] | None = None,
) -> DuplicateCandidateRead:
    """Serialize a candidate with human-readable labels and a suggested merge target."""
    return DuplicateCandidateRead.model_validate(candidate).model_copy(
        update=describe_candidate(db, candidate, works=works, files=files)
    )


class DuplicateScanRequest(BaseModel):
    work_id: uuid.UUID | None = None
    file_id: uuid.UUID | None = None
    # Run a full-library scan in the background worker instead of inline (avoids a slow request).
    background: bool = False


class DuplicateScanResult(BaseModel):
    scanned_works: int
    scanned_files: int
    candidate_count: int
    candidates: list[DuplicateCandidateRead]
    queued: bool = False
    job_id: str | None = None


class FileSplitSegment(BaseModel):
    title: str
    page_start: int | None = None
    page_end: int | None = None
    label: str | None = None


class DuplicateCandidateUpdate(BaseModel):
    status: CandidateStatus | None = None
    action: DuplicateAction | None = None
    target_work_id: uuid.UUID | None = None
    split_segments: list[FileSplitSegment] | None = None


@router.get("", response_model=list[DuplicateCandidateRead])
def list_duplicate_candidates(
    status_filter: CandidateStatus | None = Query(default="open", alias="status"),
    candidate_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = DB_DEP,
    actor: User = AUTH_DEP,
) -> list[DuplicateCandidateRead]:
    """List duplicate/version candidates for review (hiding candidates that touch a hidden work)."""
    stmt = select(DuplicateCandidate)
    if status_filter:
        stmt = stmt.where(DuplicateCandidate.status == status_filter)
    if candidate_type:
        stmt = stmt.where(DuplicateCandidate.candidate_type == candidate_type)
    stmt = stmt.order_by(DuplicateCandidate.created_at.desc()).limit(limit)
    visible = access.visible_work_ids(db, actor)
    candidates = [
        candidate
        for candidate in db.scalars(stmt).all()
        if _candidate_visible(candidate, visible)
    ]
    works, files = candidate_entity_maps(db, candidates)
    return [_candidate_view(db, candidate, works=works, files=files) for candidate in candidates]


@router.post("/scan", response_model=DuplicateScanResult, status_code=status.HTTP_201_CREATED)
def scan_duplicates(
    payload: DuplicateScanRequest,
    db: Session = DB_DEP,
    actor: User = EDITOR_DEP,
) -> DuplicateScanResult:
    """Scan selected or all known work/file identities for duplicate candidates.

    Only papers/files the caller may SEE are scanned (a full-library scan skips hidden works)."""
    # A full-library scan (no specific work/file) can be pushed to the background worker.
    if payload.background and payload.work_id is None and payload.file_id is None:
        from app.workers.queue import enqueue_duplicate_scan

        job_id = enqueue_duplicate_scan()
        if job_id is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Duplicate-scan queue unavailable",
            )
        return DuplicateScanResult(
            scanned_works=0,
            scanned_files=0,
            candidate_count=0,
            candidates=[],
            queued=True,
            job_id=job_id,
        )

    works = _selected_works(db, payload.work_id, actor=actor)
    files = _selected_files(db, payload.file_id, actor=actor)

    candidates: list[DuplicateCandidate] = []
    for work in works:
        candidates.extend(scan_duplicate_candidates(db, work=work))
    for file in files:
        candidates.extend(scan_duplicate_candidates(db, file=file))

    candidate_ids = [candidate.id for candidate in candidates]
    db.commit()
    if candidate_ids:
        # One IN() reload re-populates the committed (expired) rows instead of a refresh per row.
        db.scalars(
            select(DuplicateCandidate).where(DuplicateCandidate.id.in_(candidate_ids))
        ).all()
    entity_works, entity_files = candidate_entity_maps(db, candidates)
    return DuplicateScanResult(
        scanned_works=len(works),
        scanned_files=len(files),
        candidate_count=len(candidates),
        candidates=[
            _candidate_view(db, candidate, works=entity_works, files=entity_files)
            for candidate in candidates
        ],
    )


@router.patch("/{candidate_id}", response_model=DuplicateCandidateRead)
def update_duplicate_candidate(
    candidate_id: uuid.UUID,
    payload: DuplicateCandidateUpdate,
    db: Session = DB_DEP,
    actor: User = EDITOR_DEP,
) -> DuplicateCandidateRead:
    """Update review status or apply a duplicate/version review action."""
    candidate = db.get(DuplicateCandidate, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate not found")
    if not _candidate_visible(candidate, access.visible_work_ids(db, actor)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate not found")

    if payload.action and payload.status:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Send either action or status, not both",
        )
    try:
        if payload.action:
            if payload.action == "split_file":
                split_multiwork_file(
                    db,
                    candidate=candidate,
                    actor=actor,
                    segments=[
                        segment.model_dump(exclude_none=True)
                        for segment in payload.split_segments or []
                    ],
                )
            else:
                apply_duplicate_action(
                    db,
                    candidate=candidate,
                    action=payload.action,
                    actor=actor,
                    target_work_id=payload.target_work_id,
                )
        elif payload.status == "open":
            reopen_duplicate_candidate(candidate)
        elif payload.status:
            candidate.status = payload.status
            candidate.resolved_by_user_id = actor.id
            candidate.resolved_at = datetime.now(UTC)
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Either action or status is required",
            )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    db.refresh(candidate)
    return _candidate_view(db, candidate)


def _selected_works(db: Session, work_id: uuid.UUID | None, *, actor: User) -> list[Work]:
    if work_id is None:
        return list(db.scalars(access.visible_works_query(db, actor)).all())
    work = db.get(Work, work_id)
    if work is None or not access.can_see_work(db, actor, work):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    return [work]


def _selected_files(db: Session, file_id: uuid.UUID | None, *, actor: User) -> list[File]:
    if file_id is None:
        files = list(db.scalars(select(File)).all())
        if access.is_admin_or_owner(actor):
            return files
        # Batched visibility: compute the visible-work set once and fetch all file->work links
        # in one IN() query (instead of a can_see_file scan per file).
        visible = access.visible_work_ids(db, actor)
        linked_works: dict[uuid.UUID, list[uuid.UUID]] = {}
        for fid, wid in db.execute(
            select(FileWorkLink.file_id, FileWorkLink.work_id).where(
                FileWorkLink.file_id.in_([f.id for f in files])
            )
        ):
            linked_works.setdefault(fid, []).append(wid)
        return [
            f
            for f in files
            if f.id not in linked_works  # loose file -> open
            or visible is None
            or any(wid in visible for wid in linked_works[f.id])
        ]
    file = db.get(File, file_id)
    if file is None or not access.can_see_file(db, actor, file_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    return [file]
