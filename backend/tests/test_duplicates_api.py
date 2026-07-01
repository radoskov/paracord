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
from app.models.audit import AuditEvent
from app.models.duplicate import DuplicateCandidate
from app.models.file import File, FileSegment, FileWorkLink
from app.models.organization import Shelf, ShelfWork, Tag, TagLink
from app.models.user import User
from app.models.work import Work, WorkVersion
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

# Heavier suite: slow per-test schema setup (full Base.metadata create_all on file-backed SQLite)
# — moved to the full tier. Run via `make test-full`/`make ready-full` or `pytest -m slow`.
pytestmark = pytest.mark.slow


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'duplicates-api.db'}")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            User.__table__,
            AuditEvent.__table__,
            Work.__table__,
            WorkVersion.__table__,
            File.__table__,
            FileSegment.__table__,
            FileWorkLink.__table__,
            Shelf.__table__,
            ShelfWork.__table__,
            Tag.__table__,
            TagLink.__table__,
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
        doi="10.5555/transformer",  # stored normalized (DOI normalize happens at write time)
    )
    db_session.add_all([first, second])
    db_session.commit()

    result = scan_duplicates(DuplicateScanRequest(work_id=first.id), db=db_session, actor=editor)

    assert result.scanned_works == 1
    assert result.candidate_count == 1
    assert result.candidates[0].candidate_type == "same_doi"
    listed = list_duplicate_candidates(
        status_filter="open",
        candidate_type=None,
        limit=100,
        db=db_session,
        actor=editor,
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


def test_merge_works_relinks_memberships_without_deleting_source(db_session, editor: User) -> None:
    target = Work(canonical_title="Target", normalized_title="target")
    source = Work(canonical_title="Source", normalized_title="source")
    file = File(sha256="a" * 64, size_bytes=1)
    shelf = Shelf(name="Reading")
    tag = Tag(name="Duplicate", normalized_name="duplicate")
    db_session.add_all([target, source, file, shelf, tag])
    db_session.flush()
    db_session.add_all(
        [
            FileWorkLink(file_id=file.id, work_id=source.id),
            ShelfWork(shelf_id=shelf.id, work_id=source.id, added_by_user_id=editor.id),
            TagLink(tag_id=tag.id, entity_type="work", entity_id=source.id),
        ]
    )
    candidate = DuplicateCandidate(
        candidate_type="same_doi",
        entity_a_type="work",
        entity_a_id=target.id,
        entity_b_type="work",
        entity_b_id=source.id,
        score=1.0,
        signals={},
    )
    db_session.add(candidate)
    db_session.commit()

    updated = update_duplicate_candidate(
        candidate.id,
        DuplicateCandidateUpdate(action="merge_works", target_work_id=target.id),
        db=db_session,
        actor=editor,
    )

    assert updated.status == "accepted"
    assert db_session.get(Work, source.id).work_type == "merged"
    assert (
        db_session.scalar(select(FileWorkLink).where(FileWorkLink.file_id == file.id)).work_id
        == target.id
    )
    assert db_session.get(ShelfWork, {"shelf_id": shelf.id, "work_id": target.id}) is not None
    assert (
        db_session.get(
            TagLink,
            {"tag_id": tag.id, "entity_type": "work", "entity_id": target.id},
        )
        is not None
    )
    assert db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == "duplicate_candidate.resolved")
    )


def test_link_as_version_creates_version_and_relinks_file(db_session, editor: User) -> None:
    target = Work(canonical_title="Paper", normalized_title="paper", arxiv_id="1706.03762v1")
    source = Work(
        canonical_title="Paper v2",
        normalized_title="paper v2",
        arxiv_id="1706.03762v2",
        doi="10.5555/paper",
    )
    file = File(sha256="b" * 64, size_bytes=1)
    db_session.add_all([target, source, file])
    db_session.flush()
    db_session.add(FileWorkLink(file_id=file.id, work_id=source.id))
    candidate = DuplicateCandidate(
        candidate_type="same_arxiv",
        entity_a_type="work",
        entity_a_id=target.id,
        entity_b_type="work",
        entity_b_id=source.id,
        score=0.99,
        signals={"version_mismatch": True},
    )
    db_session.add(candidate)
    db_session.commit()

    update_duplicate_candidate(
        candidate.id,
        DuplicateCandidateUpdate(action="link_as_version", target_work_id=target.id),
        db=db_session,
        actor=editor,
    )

    version = db_session.scalar(select(WorkVersion))
    assert version.work_id == target.id
    assert version.version_type == "arxiv"
    assert version.arxiv_version == "v2"
    link = db_session.scalar(select(FileWorkLink).where(FileWorkLink.file_id == file.id))
    assert link.work_id == target.id
    assert link.version_id == version.id
    assert link.warning_state == "work_has_multiple_files"
    # Phase B6: both works join the target's version group (target is the representative).
    db_session.refresh(target)
    db_session.refresh(source)
    assert target.version_group_id == target.id
    assert source.version_group_id == target.id


def test_mark_duplicate_file_marks_file_links(db_session, editor: User) -> None:
    work = Work(canonical_title="Paper", normalized_title="paper")
    primary = File(sha256="c" * 64, size_bytes=1)
    duplicate = File(sha256="d" * 64, size_bytes=1)
    db_session.add_all([work, primary, duplicate])
    db_session.flush()
    db_session.add_all(
        [
            FileWorkLink(file_id=primary.id, work_id=work.id),
            FileWorkLink(file_id=duplicate.id, work_id=work.id),
        ]
    )
    candidate = DuplicateCandidate(
        candidate_type="exact_file",
        entity_a_type="file",
        entity_a_id=primary.id,
        entity_b_type="file",
        entity_b_id=duplicate.id,
        score=1.0,
        signals={},
    )
    db_session.add(candidate)
    db_session.commit()

    update_duplicate_candidate(
        candidate.id,
        DuplicateCandidateUpdate(action="mark_duplicate_file"),
        db=db_session,
        actor=editor,
    )

    duplicate_link = db_session.scalar(
        select(FileWorkLink).where(FileWorkLink.file_id == duplicate.id)
    )
    assert duplicate_link.relationship_type == "duplicate_copy"
    assert duplicate_link.warning_state == "work_has_multiple_files"
    assert duplicate_link.user_confirmed is True


def test_split_file_creates_segments_works_and_links(db_session, editor: User) -> None:
    file = File(sha256="e" * 64, size_bytes=100, page_count=12)
    db_session.add(file)
    db_session.flush()
    candidate = DuplicateCandidate(
        candidate_type="multiwork_file",
        entity_a_type="file",
        entity_a_id=file.id,
        entity_b_type="file",
        entity_b_id=file.id,
        score=0.78,
        signals={},
    )
    db_session.add(candidate)
    db_session.commit()

    updated = update_duplicate_candidate(
        candidate.id,
        DuplicateCandidateUpdate(
            action="split_file",
            split_segments=[
                {"title": "First Paper", "page_start": 1, "page_end": 6},
                {"title": "Second Paper", "page_start": 7, "page_end": 12},
            ],
        ),
        db=db_session,
        actor=editor,
    )

    assert updated.status == "accepted"
    assert updated.signals["split_segment_count"] == 2
    works = db_session.scalars(select(Work).order_by(Work.canonical_title)).all()
    assert [work.canonical_title for work in works] == ["First Paper", "Second Paper"]
    segments = db_session.scalars(select(FileSegment).order_by(FileSegment.page_start)).all()
    assert [(segment.page_start, segment.page_end) for segment in segments] == [(1, 6), (7, 12)]
    links = db_session.scalars(select(FileWorkLink).order_by(FileWorkLink.created_at)).all()
    assert {link.relationship_type for link in links} == {"contains"}
    assert {link.warning_state for link in links} == {"file_contains_multiple_works"}


def _work_pair_candidate(db_session, work_a: Work, work_b: Work, **kwargs) -> DuplicateCandidate:
    candidate = DuplicateCandidate(
        candidate_type=kwargs.pop("candidate_type", "fuzzy_title"),
        entity_a_type="work",
        entity_a_id=work_a.id,
        entity_b_type="work",
        entity_b_id=work_b.id,
        score=kwargs.pop("score", 0.95),
        signals=kwargs.pop("signals", {}),
    )
    db_session.add(candidate)
    db_session.commit()
    return candidate


def test_applying_action_to_resolved_candidate_is_rejected(db_session, editor: User) -> None:
    target = Work(canonical_title="Keep", normalized_title="keep")
    source = Work(canonical_title="Drop", normalized_title="drop")
    db_session.add_all([target, source])
    db_session.flush()
    candidate = _work_pair_candidate(db_session, target, source, candidate_type="same_doi")

    update_duplicate_candidate(
        candidate.id,
        DuplicateCandidateUpdate(action="merge_works", target_work_id=target.id),
        db=db_session,
        actor=editor,
    )
    # A second merge (e.g. a double-click) must be refused, not silently re-applied.
    with pytest.raises(HTTPException) as exc:
        update_duplicate_candidate(
            candidate.id,
            DuplicateCandidateUpdate(action="merge_works", target_work_id=target.id),
            db=db_session,
            actor=editor,
        )
    assert exc.value.status_code == 400
    assert "already been resolved" in exc.value.detail


def test_splitting_same_file_twice_is_rejected(db_session, editor: User) -> None:
    file = File(sha256="f" * 64, size_bytes=100, page_count=10)
    db_session.add(file)
    db_session.flush()
    candidate = DuplicateCandidate(
        candidate_type="multiwork_file",
        entity_a_type="file",
        entity_a_id=file.id,
        entity_b_type="file",
        entity_b_id=file.id,
        score=0.78,
        signals={},
    )
    db_session.add(candidate)
    db_session.commit()

    segments = [{"title": "One", "page_start": 1, "page_end": 5}]
    update_duplicate_candidate(
        candidate.id,
        DuplicateCandidateUpdate(action="split_file", split_segments=segments),
        db=db_session,
        actor=editor,
    )
    # Reopening clears the status but not the created works, so a re-split is still refused.
    update_duplicate_candidate(
        candidate.id, DuplicateCandidateUpdate(status="open"), db=db_session, actor=editor
    )
    with pytest.raises(HTTPException) as exc:
        update_duplicate_candidate(
            candidate.id,
            DuplicateCandidateUpdate(action="split_file", split_segments=segments),
            db=db_session,
            actor=editor,
        )
    assert exc.value.status_code == 400
    assert "already been split" in exc.value.detail
    assert len(db_session.scalars(select(Work)).all()) == 1


def test_auto_target_prefers_confirmed_then_latest_arxiv(db_session, editor: User) -> None:
    # No explicit target_work_id: the user-confirmed work should survive as canonical.
    plain = Work(canonical_title="Draft", normalized_title="draft")
    confirmed = Work(canonical_title="Final", normalized_title="final", user_confirmed=True)
    db_session.add_all([plain, confirmed])
    db_session.flush()
    candidate = _work_pair_candidate(db_session, plain, confirmed, candidate_type="same_doi")

    update_duplicate_candidate(
        candidate.id,
        DuplicateCandidateUpdate(action="merge_works"),
        db=db_session,
        actor=editor,
    )
    assert db_session.get(Work, plain.id).work_type == "merged"
    assert db_session.get(Work, confirmed.id).work_type != "merged"


def test_candidate_view_includes_labels_and_suggested_target(db_session, editor: User) -> None:
    v1 = Work(
        canonical_title="Attention v1", normalized_title="attention v1", arxiv_id="1706.03762v1"
    )
    v2 = Work(
        canonical_title="Attention v2", normalized_title="attention v2", arxiv_id="1706.03762v2"
    )
    db_session.add_all([v1, v2])
    db_session.flush()
    candidate = _work_pair_candidate(
        db_session, v1, v2, candidate_type="same_arxiv", signals={"version_mismatch": True}
    )

    [view] = list_duplicate_candidates(
        status_filter="open", candidate_type=None, limit=100, db=db_session, actor=editor
    )
    assert view.id == candidate.id
    assert {view.entity_a_label, view.entity_b_label} == {"Attention v1", "Attention v2"}
    assert "Same arXiv ID (version mismatch)" in view.summary
    # The later arXiv version is the suggested canonical target.
    assert view.suggested_target_work_id == v2.id
