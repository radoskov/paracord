"""Local summarization tests (M7, tiers 0 and 1)."""

import uuid
from pathlib import Path

import pytest
from app.db.base import Base
from app.models.ai import Summary
from app.models.citation import RawTeiDocument
from app.models.work import Work
from app.services.summarization import (
    list_work_summaries,
    summarize_extractive,
    summarize_work,
)
from app.services.tei_parser import extract_body_text
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

TEI_BODY = """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <text><body>
    <div><head>Method</head>
      <p>The transformer relies on attention. Attention replaces recurrence with attention.</p>
    </div>
    <div><head>Results</head>
      <p>Attention improves translation quality. The weather outside is unrelated and sunny.</p>
    </div>
  </body></text>
</TEI>
"""


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'summaries.db'}")
    Base.metadata.create_all(
        bind=engine,
        tables=[Work.__table__, Summary.__table__, RawTeiDocument.__table__],
    )
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with session_local() as session:
        yield session


# --- pure extractive summarizer ---------------------------------------------


def test_summarize_extractive_returns_short_text_unchanged() -> None:
    assert summarize_extractive("Only one sentence here.", max_sentences=5) == (
        "Only one sentence here."
    )


def test_summarize_extractive_selects_salient_sentences() -> None:
    text = (
        "The transformer uses attention. "
        "Attention over attention drives the transformer's attention layers. "
        "Cats are fluffy and sleep all day. "
        "The transformer's attention mechanism beats recurrence on attention tasks. "
        "Yesterday the weather was rainy and cold."
    )
    summary = summarize_extractive(text, max_sentences=2)
    assert summary.count(".") == 2  # exactly two sentences kept
    assert "attention" in summary.lower()
    assert "fluffy" not in summary.lower()  # off-topic sentence excluded
    assert "weather" not in summary.lower()


def test_extract_body_text_reads_tei_paragraphs() -> None:
    body = extract_body_text(TEI_BODY)
    assert body is not None
    assert "attention replaces recurrence" in body.lower()
    assert "weather outside" in body.lower()


# --- summarize_work ---------------------------------------------------------


def test_summarize_work_abstract_tier_stores_verbatim(db_session) -> None:
    work = Work(canonical_title="t", normalized_title="t", abstract="A concise abstract.")
    db_session.add(work)
    db_session.commit()

    summary = summarize_work(db_session, work, summary_type="abstract")
    db_session.commit()

    assert summary.text == "A concise abstract."
    assert summary.model_name == "tier0-abstract"
    assert summary.prompt_version == "v1"
    assert summary.entity_type == "work"


def test_summarize_work_extractive_uses_abstract_and_tei_body(db_session) -> None:
    work = Work(canonical_title="t", normalized_title="t", abstract="Short framing abstract.")
    db_session.add(work)
    db_session.flush()
    db_session.add(RawTeiDocument(file_id=uuid.uuid4(), work_id=work.id, tei_xml=TEI_BODY))
    db_session.commit()

    summary = summarize_work(db_session, work, summary_type="extractive", max_sentences=2)
    db_session.commit()

    assert summary.model_name == "tier1-extractive-frequency"
    assert "attention" in summary.text.lower()  # pulled from the TEI body


def test_summarize_work_is_idempotent_per_type(db_session) -> None:
    work = Work(
        canonical_title="t", normalized_title="t", abstract="One. Two. Three. Four. Five. Six."
    )
    db_session.add(work)
    db_session.commit()

    summarize_work(db_session, work, summary_type="extractive")
    db_session.commit()
    summarize_work(db_session, work, summary_type="extractive")
    db_session.commit()

    count = db_session.scalar(
        select(func.count()).select_from(Summary).where(Summary.summary_type == "extractive")
    )
    assert count == 1
    assert len(list_work_summaries(db_session, work.id)) == 1


def test_summarize_work_rejects_unknown_type(db_session) -> None:
    work = Work(canonical_title="t", normalized_title="t", abstract="x")
    db_session.add(work)
    db_session.commit()
    with pytest.raises(ValueError, match="Unsupported summary type"):
        summarize_work(db_session, work, summary_type="abstractive")


def test_summarize_work_without_text_raises(db_session) -> None:
    work = Work(canonical_title="t", normalized_title="t")  # no abstract, no TEI
    db_session.add(work)
    db_session.commit()
    with pytest.raises(ValueError, match="No text available"):
        summarize_work(db_session, work, summary_type="extractive")


# --- API surface ------------------------------------------------------------


def test_summary_api_rejects_unsupported_type(client, auth_headers, db) -> None:
    work = Work(canonical_title="t", normalized_title="t", abstract="x")
    db.add(work)
    db.commit()
    r = client.post(
        f"/api/v1/works/{work.id}/summaries",
        headers=auth_headers("editor"),
        json={"summary_type": "telepathic"},
    )
    assert r.status_code == 400


def test_summary_api_returns_provenance(client, auth_headers, db) -> None:
    work = Work(canonical_title="t", normalized_title="t", abstract="A real abstract sentence.")
    db.add(work)
    db.commit()
    created = client.post(
        f"/api/v1/works/{work.id}/summaries",
        headers=auth_headers("editor"),
        json={"summary_type": "abstract"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["model_name"] == "tier0-abstract"
    assert body["prompt_version"] == "v1"
    assert body["text"] == "A real abstract sentence."
