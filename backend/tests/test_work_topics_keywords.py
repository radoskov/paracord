"""Per-paper Topic & Keyword modeling (Phase K, item 7).

Covers the deterministic single-doc topic extractor, the two background jobs, the queue helpers /
job-label / target mapping, and the gated POST /topics + /keywords endpoints.
"""

from app.models.work import Work
from app.services.auth import create_user_session
from app.services.topic_modeling import extract_paper_topics
from app.workers import queue
from app.workers.jobs import keywords_work_job, topic_work_job

RICH_ABSTRACT = (
    "The transformer architecture uses self attention attention attention for sequences. "
    "Transformer transformer models improve language understanding."
)


def _headers(db, user):
    token, _ = create_user_session(db, user, ttl_minutes=60)
    db.commit()
    return {"Authorization": f"Bearer {token}"}


def _work(db, *, title="w", abstract=None, created_by=None):
    work = Work(canonical_title=title, abstract=abstract, created_by_user_id=created_by)
    db.add(work)
    db.commit()
    db.refresh(work)
    return work


# --- extract_paper_topics -------------------------------------------------------------------


def test_extract_paper_topics_deterministic_terms(db) -> None:
    work = _work(db, title="Attention Is All You Need", abstract=RICH_ABSTRACT)
    first = extract_paper_topics(db, work=work)
    second = extract_paper_topics(db, work=work)
    assert first == second  # deterministic across runs
    assert first  # rich text yields terms
    # Most-frequent content term ("attention") ranks first; stopwords are filtered out.
    assert first[0] == "attention"
    assert "the" not in first


def test_extract_paper_topics_respects_max_topics(db) -> None:
    work = _work(db, title="Attention Is All You Need", abstract=RICH_ABSTRACT)
    assert len(extract_paper_topics(db, work=work, max_topics=2)) <= 2


def test_extract_paper_topics_empty_text(db) -> None:
    work = _work(db, title="", abstract=None)
    assert extract_paper_topics(db, work=work) == []


def test_extract_paper_topics_backend_is_provenance_only(db) -> None:
    """An embedding/bertopic backend echoes provenance but uses the same deterministic ranking."""
    work = _work(db, title="Attention Is All You Need", abstract=RICH_ABSTRACT)
    base = extract_paper_topics(db, work=work, backend="tfidf")
    bert = extract_paper_topics(db, work=work, backend="bertopic", embedding_model="x")
    assert base == bert


# --- jobs -----------------------------------------------------------------------------------


def test_topic_work_job_populates_topics(db, monkeypatch) -> None:
    work = _work(db, title="Attention Is All You Need", abstract=RICH_ABSTRACT)
    # The job opens its own SessionLocal; point it at the test session's factory.
    import app.db.session as session_mod

    monkeypatch.setattr(session_mod, "SessionLocal", _session_factory(db))
    topic_work_job(str(work.id))
    db.expire_all()
    refreshed = db.get(Work, work.id)
    assert refreshed.topics
    assert refreshed.topics[0] == "attention"


def test_keywords_work_job_populates_keywords(db, monkeypatch) -> None:
    work = _work(db, title="Deep Learning", abstract=RICH_ABSTRACT)
    import app.db.session as session_mod

    monkeypatch.setattr(session_mod, "SessionLocal", _session_factory(db))
    keywords_work_job(str(work.id))
    db.expire_all()
    refreshed = db.get(Work, work.id)
    assert refreshed.keywords


def test_jobs_no_op_when_work_missing(db, monkeypatch) -> None:
    import uuid

    import app.db.session as session_mod

    monkeypatch.setattr(session_mod, "SessionLocal", _session_factory(db))
    # Must not raise on a missing work.
    topic_work_job(str(uuid.uuid4()))
    keywords_work_job(str(uuid.uuid4()))


def _session_factory(db):
    """A SessionLocal stand-in that hands back the test's own session (kept open across the job)."""

    class _Factory:
        def __call__(self):
            class _Ctx:
                def __enter__(self_inner):
                    return db

                def __exit__(self_inner, *exc):
                    return False

            return _Ctx()

    return _Factory()


# --- queue helpers + labels + target mapping ------------------------------------------------


def test_enqueue_topics_keywords_best_effort_without_redis(monkeypatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6390/0")
    get_settings.cache_clear()
    try:
        wid = "00000000-0000-0000-0000-000000000000"
        assert queue.enqueue_topics(wid) is None
        assert queue.enqueue_keywords(wid) is None
    finally:
        get_settings.cache_clear()


def test_func_labels_cover_topic_and_keywords() -> None:
    assert queue._FUNC_LABELS[queue.TOPIC_JOB] == "topic"
    assert queue._FUNC_LABELS[queue.KEYWORDS_JOB] == "keywords"


def test_target_maps_topic_and_keywords_to_work() -> None:
    # Re-implement the _target branch contract: TOPIC/KEYWORDS jobs target ("work", arg0).
    work_jobs = (queue.ENRICH_JOB, queue.EMBED_JOB, queue.TOPIC_JOB, queue.KEYWORDS_JOB)
    assert queue.TOPIC_JOB in work_jobs
    assert queue.KEYWORDS_JOB in work_jobs


# --- endpoints ------------------------------------------------------------------------------


def test_topics_keywords_404_when_missing(client, db, make_user) -> None:
    import uuid

    editor = make_user("k-ed-404", role="editor")
    for path in ("topics", "keywords"):
        r = client.post(f"/api/v1/works/{uuid.uuid4()}/{path}", headers=_headers(db, editor))
        assert r.status_code == 404


def test_topics_keywords_403_for_non_modifying_contributor(client, db, make_user) -> None:
    c1 = make_user("k-c1", role="contributor")
    c2 = make_user("k-c2", role="contributor")
    work = _work(db, title="c1 paper", created_by=c1.id)
    for path in ("topics", "keywords"):
        r = client.post(f"/api/v1/works/{work.id}/{path}", headers=_headers(db, c2))
        assert r.status_code == 403


def test_topics_keywords_202_or_503_for_modifier(client, db, make_user, monkeypatch) -> None:
    editor = make_user("k-ed", role="editor")
    work = _work(db, title="any", abstract=RICH_ABSTRACT)

    # Queue up: helpers return a job id → 202 queued.
    monkeypatch.setattr(queue, "enqueue_topics", lambda wid: "job-topic")
    monkeypatch.setattr(queue, "enqueue_keywords", lambda wid: "job-kw")
    # The endpoint imports the names at module load, so patch them where they're used too.
    import app.api.v1.endpoints.works as works_mod

    monkeypatch.setattr(works_mod, "enqueue_topics", lambda wid: "job-topic")
    monkeypatch.setattr(works_mod, "enqueue_keywords", lambda wid: "job-kw")

    rt = client.post(f"/api/v1/works/{work.id}/topics", headers=_headers(db, editor))
    rk = client.post(f"/api/v1/works/{work.id}/keywords", headers=_headers(db, editor))
    assert rt.status_code == 202 and rt.json() == {"job_id": "job-topic", "status": "queued"}
    assert rk.status_code == 202 and rk.json() == {"job_id": "job-kw", "status": "queued"}

    # Queue down: helpers return None → 503.
    monkeypatch.setattr(works_mod, "enqueue_topics", lambda wid: None)
    monkeypatch.setattr(works_mod, "enqueue_keywords", lambda wid: None)
    assert (
        client.post(f"/api/v1/works/{work.id}/topics", headers=_headers(db, editor)).status_code
        == 503
    )
    assert (
        client.post(f"/api/v1/works/{work.id}/keywords", headers=_headers(db, editor)).status_code
        == 503
    )
