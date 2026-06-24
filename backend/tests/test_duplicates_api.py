"""Duplicate review API tests."""

from pathlib import Path

import pytest
from app.api.v1.endpoints.duplicates import (
    DuplicateCandidateUpdate,
    DuplicateScanRequest,
    list_duplicate_candidates,
    scan_duplicates,
    update_duplicate_candidate,
)
from app.core.security import hash_password
from app.db.base import Base
from app.models.duplicate import DuplicateCandidate
from app.models.file import File
from app.models.user import User
from app.models.work import Work
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'duplicates-api.db'}")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            User.__table__,
            Work.__table__,
            File.__table__,
            DuplicateCandidate.__table__,
        ],
    )
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with session_local() as session:
        yield session


@pytest.fixture()
def editor(db_session) -> User:
    user = User(username="editor", password_hash=hash_password("secret"), role="editor")
    db_session.add(user)
    db_session.commit()
    return user


def test_scan_duplicates_endpoint_creates_and_lists_candidates(db_session, editor: User) -> None:
    first = Work(
        canonical_title="Attention Is All You Need",
        normalized_title="attention is all you need",
        doi="10.5555/transformer",
    )
    second = Work(
        canonical_title="Publisher Copy",
        normalized_title="publisher copy",
        doi="https://doi.org/10.5555/transformer",
    )
    db_session.add_all([first, second])
    db_session.commit()

    result = scan_duplicates(DuplicateScanRequest(work_id=first.id), db=db_session, _=editor)

    assert result.scanned_works == 1
    assert result.candidate_count == 1
    assert result.candidates[0].candidate_type == "same_doi"
    listed = list_duplicate_candidates(
        status_filter="open",
        candidate_type=None,
        limit=100,
        db=db_session,
    )
    assert listed[0].id == result.candidates[0].id


def test_update_duplicate_candidate_marks_resolution(db_session, editor: User) -> None:
    first = Work(canonical_title="A", normalized_title="a")
    second = Work(canonical_title="B", normalized_title="b")
    db_session.add_all([first, second])
    db_session.flush()
    candidate = DuplicateCandidate(
        candidate_type="fuzzy_title",
        entity_a_type="work",
        entity_a_id=first.id,
        entity_b_type="work",
        entity_b_id=second.id,
        score=0.95,
        signals={},
    )
    db_session.add(candidate)
    db_session.commit()

    updated = update_duplicate_candidate(
        candidate.id,
        DuplicateCandidateUpdate(status="ignored"),
        db=db_session,
        actor=editor,
    )

    assert updated.status == "ignored"
    assert updated.resolved_by_user_id == editor.id
    assert updated.resolved_at is not None

    reopened = update_duplicate_candidate(
        candidate.id,
        DuplicateCandidateUpdate(status="open"),
        db=db_session,
        actor=editor,
    )
    assert reopened.status == "open"
    assert reopened.resolved_by_user_id is None
    assert reopened.resolved_at is None
