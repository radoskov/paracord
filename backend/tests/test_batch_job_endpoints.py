"""The Library batch actions' background-job endpoints (2026-07-15): citing-fetch + summarize."""


def _make(client, headers, **kw):
    return client.post("/api/v1/works", headers=headers, json=kw).json()


def test_citing_fetch_job_requires_identifier(client, auth_headers):
    h = auth_headers("editor")
    work = _make(client, h, canonical_title="No ids")
    resp = client.post(f"/api/v1/works/{work['id']}/citing-papers/fetch-job", headers=h)
    assert resp.status_code == 400


def test_citing_fetch_job_enqueues(client, auth_headers, monkeypatch):
    h = auth_headers("editor")
    work = _make(client, h, canonical_title="Has DOI", doi="10.1/xyz")
    monkeypatch.setattr("app.api.v1.endpoints.works.enqueue_citing_fetch", lambda work_id: "job-1")
    resp = client.post(f"/api/v1/works/{work['id']}/citing-papers/fetch-job", headers=h)
    assert resp.status_code == 202
    assert resp.json() == {"job_id": "job-1", "status": "queued"}


def test_summarize_job_enqueues_and_503s_when_queue_down(client, auth_headers, monkeypatch):
    h = auth_headers("editor")
    work = _make(client, h, canonical_title="Summarizable")
    monkeypatch.setattr("app.api.v1.endpoints.works.enqueue_work_summary", lambda work_id: "job-2")
    resp = client.post(f"/api/v1/works/{work['id']}/summaries/job", headers=h)
    assert resp.status_code == 202
    assert resp.json()["job_id"] == "job-2"

    monkeypatch.setattr("app.api.v1.endpoints.works.enqueue_work_summary", lambda work_id: None)
    assert client.post(f"/api/v1/works/{work['id']}/summaries/job", headers=h).status_code == 503
