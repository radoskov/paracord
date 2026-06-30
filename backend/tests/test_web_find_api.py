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
