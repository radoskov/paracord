"""Find-on-web API tests (#5): search + download endpoints, role gates, per-item status.

HTTP egress is never real — ``find_candidates`` is monkeypatched to inject candidates and the
download streamer is replaced with a fake. Editor-role required for both endpoints.
"""

import pytest
from app.models.work import Work
from app.services.web_find import WebCandidate

_PDF_BYTES = b"%PDF-1.4\n% api test fixture\n%%EOF\n"


@pytest.fixture()
def work_id(db):
    work = Work(canonical_title="Deep Residual Learning for Image Recognition", year=2016)
    db.add(work)
    db.commit()
    db.refresh(work)
    return str(work.id)


def _candidates():
    return {
        "candidates": [
            WebCandidate(
                source="openalex",
                title="Deep Residual Learning for Image Recognition",
                doi="10.1/x",
                year=2016,
                pdf_url="https://arxiv.org/pdf/1512.03385.pdf",
                landing_url="https://arxiv.org/abs/1512.03385",
                is_oa=True,
            )
        ],
        "degraded_sources": [],
        "queried_sources": ["openalex"],
    }


# --- search -----------------------------------------------------------------


def test_find_on_web_returns_ranked(client, auth_headers, monkeypatch, work_id):
    monkeypatch.setattr("app.api.v1.endpoints.works.find_candidates", lambda *a, **k: _candidates())
    h = auth_headers("editor")
    resp = client.post(f"/api/v1/works/{work_id}/find-on-web", headers=h, json={})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["candidates"]) == 1
    assert body["candidates"][0]["is_oa"] is True
    assert body["candidates"][0]["pdf_url"].endswith(".pdf")
    assert body["degraded_sources"] == []


def test_find_on_web_source_down_returns_200_degraded(client, auth_headers, monkeypatch, work_id):
    degraded = {"candidates": [], "degraded_sources": ["crossref"], "queried_sources": ["crossref"]}
    monkeypatch.setattr("app.api.v1.endpoints.works.find_candidates", lambda *a, **k: degraded)
    h = auth_headers("editor")
    resp = client.post(f"/api/v1/works/{work_id}/find-on-web", headers=h, json={})
    assert resp.status_code == 200
    assert resp.json()["degraded_sources"] == ["crossref"]


def test_find_on_web_missing_work_404(client, auth_headers, monkeypatch):
    import uuid

    monkeypatch.setattr("app.api.v1.endpoints.works.find_candidates", lambda *a, **k: _candidates())
    h = auth_headers("editor")
    resp = client.post(f"/api/v1/works/{uuid.uuid4()}/find-on-web", headers=h, json={})
    assert resp.status_code == 404


def test_find_on_web_reader_forbidden(client, auth_headers, work_id):
    h = auth_headers("reader")
    resp = client.post(f"/api/v1/works/{work_id}/find-on-web", headers=h, json={})
    assert resp.status_code == 403


# --- download ---------------------------------------------------------------


def _patch_search(monkeypatch):
    monkeypatch.setattr("app.api.v1.endpoints.works.find_candidates", lambda *a, **k: _candidates())


def test_download_success_attaches_and_enqueues(client, auth_headers, monkeypatch, work_id):
    _patch_search(monkeypatch)
    monkeypatch.setattr(
        "app.services.web_find._stream_pdf", lambda url, *, timeout, max_bytes: _PDF_BYTES
    )
    enqueued: list = []
    monkeypatch.setattr(
        "app.services.web_find.enqueue_extraction", lambda fid: enqueued.append(fid)
    )
    h = auth_headers("editor")
    resp = client.post(
        f"/api/v1/works/{work_id}/find-on-web/download",
        headers=h,
        json={
            "items": [
                {
                    "candidate_id": "c1",
                    "url": "https://arxiv.org/pdf/1512.03385.pdf",
                    "source": "openalex",
                }
            ]
        },
    )
    assert resp.status_code == 200
    result = resp.json()["results"][0]
    assert result["status"] == "attached"
    assert result["file"] is not None
    assert result["file"]["sha256"]
    assert len(enqueued) == 1  # extraction enqueued for the new file


def test_download_dedup_returns_deduped(client, auth_headers, monkeypatch, work_id):
    _patch_search(monkeypatch)
    monkeypatch.setattr(
        "app.services.web_find._stream_pdf", lambda url, *, timeout, max_bytes: _PDF_BYTES
    )
    h = auth_headers("editor")
    url = "https://arxiv.org/pdf/1512.03385.pdf"
    payload = {"items": [{"candidate_id": "c1", "url": url, "source": "openalex"}]}
    first = client.post(f"/api/v1/works/{work_id}/find-on-web/download", headers=h, json=payload)
    assert first.json()["results"][0]["status"] == "attached"
    second = client.post(f"/api/v1/works/{work_id}/find-on-web/download", headers=h, json=payload)
    assert second.json()["results"][0]["status"] == "deduped"


def test_download_non_pdf_manual_upload_no_file(client, auth_headers, monkeypatch, work_id):
    _patch_search(monkeypatch)
    monkeypatch.setattr(
        "app.services.web_find._stream_pdf", lambda url, *, timeout, max_bytes: None
    )
    h = auth_headers("editor")
    resp = client.post(
        f"/api/v1/works/{work_id}/find-on-web/download",
        headers=h,
        json={
            "items": [
                {
                    "candidate_id": "c1",
                    "url": "https://arxiv.org/pdf/1512.03385.pdf",
                    "source": "openalex",
                }
            ]
        },
    )
    result = resp.json()["results"][0]
    assert result["status"] == "manual_upload_needed"
    assert result["file"] is None
    # No file attached.
    files = client.get(f"/api/v1/works/{work_id}/files", headers=h).json()
    assert files == []


def test_download_oversized_errors_no_file(client, auth_headers, monkeypatch, work_id):
    _patch_search(monkeypatch)

    def oversized(url, *, timeout, max_bytes):
        raise ValueError("download exceeds max size cap")

    monkeypatch.setattr("app.services.web_find._stream_pdf", oversized)
    h = auth_headers("editor")
    resp = client.post(
        f"/api/v1/works/{work_id}/find-on-web/download",
        headers=h,
        json={
            "items": [
                {
                    "candidate_id": "c1",
                    "url": "https://arxiv.org/pdf/1512.03385.pdf",
                    "source": "openalex",
                }
            ]
        },
    )
    result = resp.json()["results"][0]
    assert result["status"] == "error"
    files = client.get(f"/api/v1/works/{work_id}/files", headers=h).json()
    assert files == []


def test_download_url_not_surfaced_refused(client, auth_headers, monkeypatch, work_id):
    _patch_search(monkeypatch)
    monkeypatch.setattr(
        "app.services.web_find._stream_pdf", lambda url, *, timeout, max_bytes: _PDF_BYTES
    )
    h = auth_headers("editor")
    resp = client.post(
        f"/api/v1/works/{work_id}/find-on-web/download",
        headers=h,
        json={
            "items": [
                {"candidate_id": "c1", "url": "https://evil.example/x.pdf", "source": "openalex"}
            ]
        },
    )
    result = resp.json()["results"][0]
    assert result["status"] == "error"


def test_download_empty_items_noop(client, auth_headers, work_id):
    h = auth_headers("editor")
    resp = client.post(
        f"/api/v1/works/{work_id}/find-on-web/download", headers=h, json={"items": []}
    )
    assert resp.status_code == 200
    assert resp.json()["results"] == []


def test_download_reader_forbidden(client, auth_headers, work_id):
    h = auth_headers("reader")
    resp = client.post(
        f"/api/v1/works/{work_id}/find-on-web/download",
        headers=h,
        json={"items": [{"candidate_id": "c1", "url": "https://x/x.pdf", "source": "openalex"}]},
    )
    assert resp.status_code == 403


# --- find-on-web v2: download-policy modes, confirmation, streaming ----------


def _set_policy(client, auth_headers, policy):
    h = auth_headers("owner")
    resp = client.put("/api/v1/admin/web-find/download-policy", headers=h, json={"policy": policy})
    assert resp.status_code == 200, resp.text
    return resp


def test_download_denied_host_blocked(client, auth_headers, monkeypatch, work_id):
    monkeypatch.setattr(
        "app.services.web_find._stream_pdf", lambda url, *, timeout, max_bytes: _PDF_BYTES
    )
    h = auth_headers("editor")
    resp = client.post(
        f"/api/v1/works/{work_id}/find-on-web/download",
        headers=h,
        json={"items": [{"candidate_id": "c1", "url": "https://sci-hub.se/x.pdf", "source": "x"}]},
    )
    result = resp.json()["results"][0]
    assert result["status"] == "blocked"
    assert "shadow" in result["reason"].lower()


def test_download_unrestricted_needs_confirmation_then_confirmed_attaches(
    client, auth_headers, monkeypatch, work_id
):
    monkeypatch.setattr(
        "app.services.web_find._stream_pdf", lambda url, *, timeout, max_bytes: _PDF_BYTES
    )
    monkeypatch.setattr("app.services.web_find.enqueue_extraction", lambda fid: None)
    _set_policy(client, auth_headers, "unrestricted")
    h = auth_headers("editor")
    url = "https://random-publisher.example/x.pdf"
    # First call: not confirmed → needs_confirmation, carries the URL, no file.
    first = client.post(
        f"/api/v1/works/{work_id}/find-on-web/download",
        headers=h,
        json={"items": [{"candidate_id": "c1", "url": url, "source": "x"}]},
    ).json()["results"][0]
    assert first["status"] == "needs_confirmation"
    assert first["url"] == url
    assert first["file"] is None
    # Re-send with confirmed=true → attaches.
    second = client.post(
        f"/api/v1/works/{work_id}/find-on-web/download",
        headers=h,
        json={"items": [{"candidate_id": "c1", "url": url, "source": "x", "confirmed": True}]},
    ).json()["results"][0]
    assert second["status"] == "attached"


def test_download_policy_get_set_owner_only(client, auth_headers):
    # Owner can GET and SET.
    owner = auth_headers("owner")
    got = client.get("/api/v1/admin/web-find/download-policy", headers=owner)
    assert got.status_code == 200
    assert got.json()["policy"] == "restricted"  # default
    assert set(got.json()["allowed"]) == {"restricted", "careful", "unrestricted"}
    put = client.put(
        "/api/v1/admin/web-find/download-policy", headers=owner, json={"policy": "careful"}
    )
    assert put.status_code == 200
    assert put.json()["policy"] == "careful"
    # Non-owners are forbidden on both GET and SET.
    for role in ("admin", "editor", "reader"):
        h = auth_headers(role)
        assert client.get("/api/v1/admin/web-find/download-policy", headers=h).status_code == 403
        assert (
            client.put(
                "/api/v1/admin/web-find/download-policy", headers=h, json={"policy": "careful"}
            ).status_code
            == 403
        )


def test_download_policy_rejects_unknown_mode(client, auth_headers):
    owner = auth_headers("owner")
    resp = client.put(
        "/api/v1/admin/web-find/download-policy", headers=owner, json={"policy": "yolo"}
    )
    assert resp.status_code == 400


def test_find_on_web_stream_emits_ndjson_sequence(client, auth_headers, monkeypatch, work_id):
    """The streaming endpoint emits per-source querying/done lines then a final result line."""
    import json

    def fake_find(db, work, *, settings, sources=None, fetchers=None, on_progress=None):
        for name in ("crossref", "openalex"):
            on_progress({"type": "source", "source": name, "status": "querying"})
            on_progress({"type": "source", "source": name, "status": "done", "count": 1})
        return {
            "candidates": [
                WebCandidate(source="openalex", title="Deep Residual Learning", year=2016)
            ],
            "degraded_sources": [],
            "queried_sources": ["crossref", "openalex"],
        }

    monkeypatch.setattr("app.api.v1.endpoints.works.find_candidates", fake_find)
    h = auth_headers("editor")
    resp = client.post(f"/api/v1/works/{work_id}/find-on-web/stream", headers=h, json={})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    lines = [json.loads(line) for line in resp.text.splitlines() if line.strip()]
    # 4 per-source progress lines + 1 final result line.
    assert lines[0] == {"type": "source", "source": "crossref", "status": "querying"}
    assert lines[1] == {"type": "source", "source": "crossref", "status": "done", "count": 1}
    assert lines[2] == {"type": "source", "source": "openalex", "status": "querying"}
    assert lines[3] == {"type": "source", "source": "openalex", "status": "done", "count": 1}
    final = lines[-1]
    assert final["type"] == "result"
    assert len(final["candidates"]) == 1
    assert final["queried_sources"] == ["crossref", "openalex"]
    assert final["degraded_sources"] == []


def test_find_on_web_stream_reader_forbidden(client, auth_headers, work_id):
    h = auth_headers("reader")
    resp = client.post(f"/api/v1/works/{work_id}/find-on-web/stream", headers=h, json={})
    assert resp.status_code == 403
