"""Duplicate/version review endpoints."""

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.security import Role
from app.db.session import get_db
from app.models.duplicate import DuplicateCandidate
from app.models.file import File
from app.models.user import User
from app.models.work import Work
from app.services.duplicate_detection import scan_duplicate_candidates
from app.services.duplicate_resolution import (
    apply_duplicate_action,
    describe_candidate,
    reopen_duplicate_candidate,
    split_multiwork_file,
)

router = APIRouter()
DB_DEP = Depends(get_db)
EDITOR_DEP = Depends(require_roles(Role.OWNER, Role.EDITOR))

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


def _candidate_view(db: Session, candidate: DuplicateCandidate) -> DuplicateCandidateRead:
    """Serialize a candidate with human-readable labels and a suggested merge target."""
    return DuplicateCandidateRead.model_validate(candidate).model_copy(
        update=describe_candidate(db, candidate)
    )


class DuplicateScanRequest(BaseModel):
    work_id: uuid.UUID | None = None
    file_id: uuid.UUID | None = None


class DuplicateScanResult(BaseModel):
    scanned_works: int
    scanned_files: int
    candidate_count: int
    candidates: list[DuplicateCandidateRead]


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
) -> list[DuplicateCandidateRead]:
    """List duplicate/version candidates for review."""
    stmt = select(DuplicateCandidate)
    if status_filter:
        stmt = stmt.where(DuplicateCandidate.status == status_filter)
    if candidate_type:
        stmt = stmt.where(DuplicateCandidate.candidate_type == candidate_type)
    stmt = stmt.order_by(DuplicateCandidate.created_at.desc()).limit(limit)
    return [_candidate_view(db, candidate) for candidate in db.scalars(stmt).all()]


@router.post("/scan", response_model=DuplicateScanResult, status_code=status.HTTP_201_CREATED)
def scan_duplicates(
    payload: DuplicateScanRequest,
    db: Session = DB_DEP,
    _: User = EDITOR_DEP,
) -> DuplicateScanResult:
    """Scan selected or all known work/file identities for duplicate candidates."""
    works = _selected_works(db, payload.work_id)
    files = _selected_files(db, payload.file_id)

    candidates: list[DuplicateCandidate] = []
    for work in works:
        candidates.extend(scan_duplicate_candidates(db, work=work))
    for file in files:
        candidates.extend(scan_duplicate_candidates(db, file=file))

    db.commit()
    for candidate in candidates:
        db.refresh(candidate)
    return DuplicateScanResult(
        scanned_works=len(works),
        scanned_files=len(files),
        candidate_count=len(candidates),
        candidates=[_candidate_view(db, candidate) for candidate in candidates],
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


def _selected_works(db: Session, work_id: uuid.UUID | None) -> list[Work]:
    if work_id is None:
        return list(db.scalars(select(Work)).all())
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    return [work]


def _selected_files(db: Session, file_id: uuid.UUID | None) -> list[File]:
    if file_id is None:
        return list(db.scalars(select(File)).all())
    file = db.get(File, file_id)
    if file is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    return [file]
