"""Duplicate/version candidate detection tests."""

from pathlib import Path

import pytest
from app.db.base import Base
from app.models.duplicate import DuplicateCandidate
from app.models.file import File
from app.models.work import Work
from app.services.duplicate_detection import scan_duplicate_candidates, split_arxiv_id
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'duplicates.db'}")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            Work.__table__,
            File.__table__,
            DuplicateCandidate.__table__,
        ],
    )
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with session_local() as session:
        yield session


def test_scan_work_candidates_finds_doi_arxiv_and_fuzzy_title(db_session) -> None:
    target = Work(
        canonical_title="Attention Is All You Need",
        normalized_title="attention is all you need",
        doi="10.5555/TRANSFORMER",
        arxiv_id="1706.03762v1",
        year=2017,
    )
    same_doi = Work(
        canonical_title="Publisher Copy",
        normalized_title="publisher copy",
        doi="10.5555/transformer",
        year=2017,
    )
    same_arxiv = Work(
        canonical_title="Attention Is All You Need v2",
        normalized_title="attention is all you need v2",
        arxiv_id="1706.03762v2",
        year=2017,
    )
    fuzzy = Work(
        canonical_title="Attention is All You Need!",
        normalized_title="attention is all you need",
        year=2017,
    )
    different_year = Work(
        canonical_title="Attention Is All You Need",
        normalized_title="attention is all you need",
        year=2020,
    )
    db_session.add_all([target, same_doi, same_arxiv, fuzzy, different_year])
    db_session.commit()

    candidates = scan_duplicate_candidates(db_session, work=target)
    db_session.commit()

    initial_count = len(candidates)
    by_type = {candidate.candidate_type: candidate for candidate in candidates}
    assert set(by_type) == {"same_doi", "same_arxiv", "fuzzy_title"}
    assert by_type["same_doi"].signals == {"doi": "10.5555/transformer"}
    assert by_type["same_arxiv"].signals["arxiv_base_id"] == "1706.03762"
    assert by_type["same_arxiv"].signals["version_mismatch"] is True
    assert by_type["fuzzy_title"].score >= 0.92

    scan_duplicate_candidates(db_session, work=target)
    db_session.commit()
    assert len(db_session.scalars(select(DuplicateCandidate)).all()) == initial_count


def test_scan_file_candidates_finds_text_fingerprint_match(db_session) -> None:
    target = File(
        sha256="a" * 64,
        size_bytes=10,
        mime_type="application/pdf",
        text_fingerprint="fp-1",
    )
    other = File(
        sha256="b" * 64,
        size_bytes=12,
        mime_type="application/pdf",
        text_fingerprint="fp-1",
    )
    db_session.add_all([target, other])
    db_session.commit()

    candidates = scan_duplicate_candidates(db_session, file=target)
    db_session.commit()

    assert len(candidates) == 1
    assert candidates[0].candidate_type == "text_fingerprint"
    assert candidates[0].signals == {"text_fingerprint": "fp-1"}


def test_split_arxiv_id_handles_versioned_and_url_forms() -> None:
    assert split_arxiv_id("arXiv:1706.03762v5") == {"base": "1706.03762", "version": "v5"}
    assert split_arxiv_id("https://arxiv.org/abs/2106.01345") == {
        "base": "2106.01345",
        "version": None,
    }
