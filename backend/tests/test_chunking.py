"""Chunking tests (HS1): section-aware passage splitting + idempotent re-chunk."""

import uuid
from pathlib import Path

import pytest
from app.db.base import Base
from app.models.chunk import WorkChunk
from app.models.citation import RawTeiDocument
from app.models.work import Work
from app.services.chunking import (
    CHUNK_MAX_TOKENS,
    build_chunks_for_work,
    chunk_text,
    iter_work_sections,
    rechunk_work,
)
from app.services.tei_parser import extract_sections
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

TEI = """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <text><body>
    <div><head>Introduction</head><p>The intro discusses background material at length.</p></div>
    <div><head>Methods</head><p>We propose a graph neural network for molecular prediction.</p></div>
    <div><head>Acknowledgments</head><p>We thank the funding agency and our colleagues.</p></div>
  </body></text>
</TEI>"""


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'chunk.db'}")
    Base.metadata.create_all(
        bind=engine,
        tables=[Work.__table__, WorkChunk.__table__, RawTeiDocument.__table__],
    )
    with sessionmaker(bind=engine, autocommit=False, autoflush=False)() as session:
        yield session


# --- chunk_text -------------------------------------------------------------


def test_chunk_text_empty_returns_empty() -> None:
    assert chunk_text("") == []
    assert chunk_text("   ") == []


def test_chunk_text_short_is_single_chunk() -> None:
    assert chunk_text("One short sentence here.") == ["One short sentence here."]


def test_chunk_text_respects_max_tokens_and_overlaps() -> None:
    # 40 sentences of 20 words each = 800 tokens -> must split, and never exceed the cap.
    text = " ".join(f"word{j} " * 19 + "end." for j in range(40))
    chunks = chunk_text(text, target=200, max_tokens=256, overlap=30)
    assert len(chunks) >= 2
    assert all(len(c.split()) <= CHUNK_MAX_TOKENS for c in chunks)
    assert all(len(c.split()) <= 256 for c in chunks)
    # Consecutive chunks share an overlapping word suffix/prefix.
    first_tail = chunks[0].split()[-10:]
    assert any(w in chunks[1].split()[:40] for w in first_tail)


def test_chunk_text_splits_monster_sentence() -> None:
    monster = "x " * 1000  # one 1000-token "sentence", no terminator
    chunks = chunk_text(monster, target=200, max_tokens=256, overlap=0)
    assert chunks
    assert all(len(c.split()) <= 256 for c in chunks)


# --- extract_sections -------------------------------------------------------


def test_extract_sections_reads_body_divs() -> None:
    sections = extract_sections(TEI)
    labels = [label for label, _ in sections]
    assert "Introduction" in labels
    assert "Methods" in labels
    assert "Acknowledgments" in labels


def test_extract_sections_malformed_is_empty() -> None:
    assert extract_sections("<not xml") == []
    assert extract_sections("") == []


# --- work-level chunking ----------------------------------------------------


def _seed_work(db) -> Work:
    work = Work(
        canonical_title="Graph Neural Networks",
        normalized_title="gnn",
        abstract="A study of message passing over graph structured data.",
    )
    db.add(work)
    db.flush()
    db.add(RawTeiDocument(file_id=uuid.uuid4(), work_id=work.id, source="grobid", tei_xml=TEI))
    db.commit()
    return work


def test_sections_include_title_abstract_and_skip_acknowledgments(db_session) -> None:
    work = _seed_work(db_session)
    labels = [label for label, _ in iter_work_sections(db_session, work)]
    assert labels[0] == "title"
    assert labels[1] == "abstract"
    assert "Methods" in labels
    assert "Introduction" in labels
    # Acknowledgments is dropped as noise.
    assert "Acknowledgments" not in labels


def test_build_chunks_positions_are_sequential(db_session) -> None:
    work = _seed_work(db_session)
    records = build_chunks_for_work(db_session, work)
    assert [r["position"] for r in records] == list(range(len(records)))
    assert all(r["token_count"] == len(r["text"].split()) for r in records)
    assert records[0]["section"] == "title"


def test_rechunk_is_idempotent(db_session) -> None:
    work = _seed_work(db_session)
    n1 = rechunk_work(db_session, work)
    db_session.commit()
    assert n1 > 0
    assert db_session.scalar(select(func.count()).select_from(WorkChunk)) == n1
    # Re-running replaces (does not duplicate) the chunks.
    n2 = rechunk_work(db_session, work)
    db_session.commit()
    assert n2 == n1
    assert db_session.scalar(select(func.count()).select_from(WorkChunk)) == n1


def test_work_with_no_tei_chunks_title_and_abstract(db_session) -> None:
    work = Work(canonical_title="Titleonly", normalized_title="t", abstract="An abstract sentence.")
    db_session.add(work)
    db_session.commit()
    labels = [label for label, _ in iter_work_sections(db_session, work)]
    assert labels == ["title", "abstract"]
    assert rechunk_work(db_session, work) == 2
