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


def test_jobs_endpoint_requires_auth(client) -> None:
    assert client.get("/api/v1/jobs").status_code == 401
