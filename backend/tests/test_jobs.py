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
