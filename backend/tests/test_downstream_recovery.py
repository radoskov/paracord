"""F2 — downstream (chunk/embed) recovery + loud per-paper failures for enrich/keyword/topic."""

import uuid

import pytest
from app.models.ai import Embedding
from app.models.chunk import WorkChunk
from app.models.file import File, FileWorkLink
from app.models.work import Work
from app.utils.normalization import normalize_title


def _work(db, title, **fields) -> Work:
    w = Work(canonical_title=title, normalized_title=normalize_title(title), **fields)
    db.add(w)
    db.flush()
    return w


def _extracted_file_for(db, work, sha) -> None:
    f = File(sha256=sha, size_bytes=1, status="extracted")
    db.add(f)
    db.flush()
    db.add(FileWorkLink(file_id=f.id, work_id=work.id))
    db.flush()


def test_owed_downstream_queries(db) -> None:
    from app.workers.recovery import owed_chunk_work_ids, owed_embedding_work_ids

    missing = _work(db, "Missing both")
    _extracted_file_for(db, missing, "a" * 64)

    indexed = _work(db, "Fully indexed")
    _extracted_file_for(db, indexed, "b" * 64)
    db.add(WorkChunk(work_id=indexed.id, position=0, text="x", token_count=1))
    db.add(
        Embedding(
            entity_type="work", entity_id=indexed.id, model_name="hash-bow-v1", dim=1, vector=[0.0]
        )
    )

    not_extracted = _work(db, "Not extracted")
    nf = File(sha256="c" * 64, size_bytes=1, status="available")
    db.add(nf)
    db.flush()
    db.add(FileWorkLink(file_id=nf.id, work_id=not_extracted.id))

    shadow = _work(db, "Merged shadow", merged_into_id=missing.id)
    _extracted_file_for(db, shadow, "d" * 64)
    db.commit()

    chunk_owed = set(owed_chunk_work_ids(db))
    embed_owed = set(owed_embedding_work_ids(db))

    assert missing.id in chunk_owed and missing.id in embed_owed
    assert indexed.id not in chunk_owed and indexed.id not in embed_owed  # already indexed
    assert not_extracted.id not in chunk_owed  # extraction never completed
    assert shadow.id not in chunk_owed and shadow.id not in embed_owed  # merged shadow excluded


def test_processing_error_helpers_are_stage_scoped(session_factory, monkeypatch) -> None:
    monkeypatch.setattr("app.db.session.SessionLocal", session_factory)
    from app.workers.jobs import _clear_work_processing_error, _set_work_processing_error

    s = session_factory()
    w = _work(s, "Paper")
    s.commit()
    wid = str(w.id)
    s.close()

    def _reload() -> Work | None:
        s2 = session_factory()
        try:
            return s2.get(Work, uuid.UUID(wid))
        finally:
            s2.close()

    _set_work_processing_error(wid, "enrich", "boom")
    assert _reload().processing_error == "enrich: boom"

    _clear_work_processing_error(wid, "keywords")  # different stage → must NOT clear
    assert _reload().processing_error == "enrich: boom"

    _clear_work_processing_error(wid, "enrich")  # same stage → clears
    assert _reload().processing_error is None


def test_topic_job_failure_flags_the_paper(session_factory, monkeypatch) -> None:
    monkeypatch.setattr("app.db.session.SessionLocal", session_factory)

    def _boom(*_a, **_k):
        raise RuntimeError("topic boom")

    monkeypatch.setattr("app.services.topic_modeling.extract_paper_topics", _boom)
    from app.workers.jobs import topic_work_job

    s = session_factory()
    w = _work(s, "Topic paper")
    s.commit()
    wid = str(w.id)
    s.close()

    with pytest.raises(RuntimeError):
        topic_work_job(wid)

    s = session_factory()
    reloaded = s.get(Work, uuid.UUID(wid))
    assert reloaded.processing_error is not None
    assert reloaded.processing_error.startswith("topics:")
    s.close()


def test_workread_exposes_processing_error(client, auth_headers, db) -> None:
    w = _work(db, "Flagged", processing_error="enrich: DOI conflict")
    db.commit()
    resp = client.get(f"/api/v1/works/{w.id}", headers=auth_headers("reader"))
    assert resp.status_code == 200
    assert resp.json()["processing_error"] == "enrich: DOI conflict"
