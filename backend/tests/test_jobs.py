"""Background-job status endpoint tests."""

_COUNT_KEYS = {"queued", "started", "finished", "failed", "scheduled", "deferred"}


def test_jobs_endpoint_returns_status_shape(client, auth_headers) -> None:
    # Works whether or not Redis is reachable: available flips, but the shape is stable.
    response = client.get("/api/v1/jobs", headers=auth_headers("reader"))
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["available"], bool)
    assert set(body["counts"]) == _COUNT_KEYS
    assert isinstance(body["jobs"], list)
    assert "workers" in body


def test_jobs_carry_optional_paper_target_keys(client, auth_headers) -> None:
    # Additive #11 fields are always present on each job row (null when there's no paper target
    # or the DB lookup is unavailable). Empty list when Redis is unreachable is fine.
    body = client.get("/api/v1/jobs", headers=auth_headers("reader")).json()
    for job in body["jobs"]:
        for key in ("target_kind", "target_id", "paper_title", "paper_sha256"):
            assert key in job


def test_jobs_endpoint_requires_auth(client) -> None:
    assert client.get("/api/v1/jobs").status_code == 401


def test_clear_jobs_requires_editor_and_returns_shape(client, auth_headers) -> None:
    # Reader may not clear; editor may. Shape is stable whether or not Redis is reachable.
    assert client.post("/api/v1/jobs/clear", headers=auth_headers("reader")).status_code == 403
    ok = client.post("/api/v1/jobs/clear", headers=auth_headers("editor"))
    assert ok.status_code == 200
    body = ok.json()
    assert isinstance(body["available"], bool)
    assert isinstance(body["cleared"], int)


def test_clear_jobs_rejects_bad_which(client, auth_headers) -> None:
    bad = client.post("/api/v1/jobs/clear?which=nonsense", headers=auth_headers("owner"))
    assert bad.status_code == 422


def test_reindex_job_provisions_before_backfill(monkeypatch) -> None:
    """D22: HNSW provisioning commits in its own txn BEFORE the long per-chunk backfill loop."""
    import contextlib

    from app.workers import jobs

    order: list[str] = []

    class _FakeSession:
        def commit(self) -> None:
            order.append("commit")

    @contextlib.contextmanager
    def _fake_session_local():
        yield _FakeSession()

    class _Provider:
        model_name = "st:fake"

        def embed(self, text: str):
            return [0.0]

    monkeypatch.setattr("app.db.session.SessionLocal", _fake_session_local)
    monkeypatch.setattr("app.services.embeddings.get_embedding_provider", lambda **k: _Provider())
    monkeypatch.setattr(
        "app.services.semantic_search.ensure_work_embeddings",
        lambda db, **k: order.append("ensure_docs") or 0,
    )
    monkeypatch.setattr(
        "app.services.embedding_registry.register_provider",
        lambda db, provider: order.append("provision") or None,
    )
    monkeypatch.setattr(
        "app.services.chunk_embeddings.backfill_chunk_embeddings",
        lambda db, **k: order.append("backfill") or 0,
    )

    jobs.reindex_embeddings_job()

    # Provisioning is committed before the backfill even begins.
    assert order.index("provision") < order.index("commit") < order.index("backfill")
