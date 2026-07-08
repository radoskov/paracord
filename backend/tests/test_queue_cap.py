"""Queue-length cap + admin queue/worker controls (D39).

Covers the capacity guard (reject at cap, fail-open when the depth can't be measured), the
admin clear-queue / reset-workers endpoints (auth + graceful degradation + audit), and the
``max_queue_len`` app-config round-trip. These drive both the service layer and the HTTP layer.
"""

from __future__ import annotations

import pytest
from app.core.config import get_settings
from app.workers import queue

# Captured at import time (before the autouse fail-open shim replaces the module attribute) so the
# fail-open HTTP test can restore the *real* measurement against a closed Redis port.
from app.workers.queue import pending_queue_depth as _real_pending_queue_depth

_ONE_ENTRY = "@article{a2020, title = {Alpha}, author = {A, X}, year = {2020}}"


# --- config round-trip ------------------------------------------------------


def test_app_config_max_queue_len_round_trip(client, auth_headers):
    admin = auth_headers("owner")
    assert client.get("/api/v1/admin/app-config", headers=admin).json()["max_queue_len"] == 1000
    updated = client.patch("/api/v1/admin/app-config", headers=admin, json={"max_queue_len": 50})
    assert updated.status_code == 200
    assert updated.json()["max_queue_len"] == 50


# --- capacity guard (service layer) -----------------------------------------


def test_pending_queue_depth_fails_open_without_redis(monkeypatch):
    """A closed/unreachable Redis yields None (unmeasurable) rather than raising."""
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6390/0")
    get_settings.cache_clear()
    try:
        assert _real_pending_queue_depth() is None
    finally:
        get_settings.cache_clear()


def test_assert_capacity_allows_when_depth_unmeasurable(db, monkeypatch):
    from app.services.queue_capacity import assert_queue_has_capacity

    monkeypatch.setattr(queue, "pending_queue_depth", lambda: None)
    assert_queue_has_capacity(db)  # fail-open: no exception


def test_assert_capacity_fails_closed_when_require_redis_and_unmeasurable(db, monkeypatch):
    """E1: with PARACORD_PRODUCTION_REQUIRE_REDIS set, an unmeasurable queue rejects with 503."""
    from app.services.queue_capacity import assert_queue_has_capacity
    from fastapi import HTTPException

    monkeypatch.setattr(queue, "pending_queue_depth", lambda: None)
    monkeypatch.setenv("PARACORD_PRODUCTION_REQUIRE_REDIS", "true")
    get_settings.cache_clear()
    try:
        with pytest.raises(HTTPException) as exc:
            assert_queue_has_capacity(db)
        assert exc.value.status_code == 503
    finally:
        get_settings.cache_clear()


def test_assert_capacity_rejects_when_at_cap(db, monkeypatch):
    from app.services.queue_capacity import assert_queue_has_capacity
    from fastapi import HTTPException

    monkeypatch.setattr(queue, "pending_queue_depth", lambda: 10_000)
    with pytest.raises(HTTPException) as exc:
        assert_queue_has_capacity(db)
    assert exc.value.status_code == 429
    assert "queue is full" in str(exc.value.detail).lower()


# --- capacity guard (HTTP layer) --------------------------------------------


def test_job_creating_request_rejected_when_queue_full(client, auth_headers, monkeypatch):
    monkeypatch.setattr(queue, "pending_queue_depth", lambda: 10_000)
    resp = client.post(
        "/api/v1/imports/bibtex", headers=auth_headers("editor"), json={"content": _ONE_ENTRY}
    )
    assert resp.status_code == 429
    assert "queue is full" in resp.json()["detail"].lower()


def test_job_creating_request_proceeds_when_redis_unreachable(client, auth_headers, monkeypatch):
    """Fail-open at the HTTP layer: a raising Redis client → depth None → the import still runs."""
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6390/0")
    get_settings.cache_clear()
    monkeypatch.setattr(queue, "pending_queue_depth", _real_pending_queue_depth)
    try:
        resp = client.post(
            "/api/v1/imports/bibtex", headers=auth_headers("editor"), json={"content": _ONE_ENTRY}
        )
        assert resp.status_code == 201
    finally:
        get_settings.cache_clear()


# --- admin controls: clear queue --------------------------------------------


def test_clear_queue_requires_admin_and_records_audit(client, auth_headers, monkeypatch):
    from app.api.v1.endpoints import jobs as jobs_ep

    monkeypatch.setattr(jobs_ep, "empty_queue", lambda: {"available": True, "dropped": 3})
    assert (
        client.post("/api/v1/jobs/clear-queue", headers=auth_headers("editor")).status_code == 403
    )
    assert (
        client.post("/api/v1/jobs/clear-queue", headers=auth_headers("reader")).status_code == 403
    )
    ok = client.post("/api/v1/jobs/clear-queue", headers=auth_headers("owner"))
    assert ok.status_code == 200
    assert ok.json() == {"available": True, "dropped": 3}
    events = client.get("/api/v1/admin/audit-events", headers=auth_headers("owner")).json()["items"]
    assert any(e["event_type"] == "queue.cleared" for e in events)


def test_clear_queue_degrades_gracefully_when_redis_down(client, auth_headers, monkeypatch):
    from app.api.v1.endpoints import jobs as jobs_ep

    monkeypatch.setattr(
        jobs_ep, "empty_queue", lambda: {"available": False, "error": "down", "dropped": 0}
    )
    resp = client.post("/api/v1/jobs/clear-queue", headers=auth_headers("admin"))
    assert resp.status_code == 200  # never 500
    assert resp.json()["available"] is False


# --- admin controls: reset workers ------------------------------------------


def test_reset_workers_requires_admin_and_records_audit(client, auth_headers, monkeypatch):
    from app.api.v1.endpoints import jobs as jobs_ep

    monkeypatch.setattr(
        jobs_ep,
        "recover_stuck_jobs",
        lambda: {
            "available": True,
            "requeued": 2,
            "cleared_failed": 1,
            "note": queue.WORKER_PROCESS_RESET_HINT,
        },
    )
    assert (
        client.post("/api/v1/jobs/reset-workers", headers=auth_headers("reader")).status_code == 403
    )
    ok = client.post("/api/v1/jobs/reset-workers", headers=auth_headers("admin"))
    assert ok.status_code == 200
    body = ok.json()
    assert body["requeued"] == 2
    assert "docker compose restart worker" in body["note"]
    events = client.get("/api/v1/admin/audit-events", headers=auth_headers("owner")).json()["items"]
    assert any(e["event_type"] == "queue.workers_reset" for e in events)


def test_reset_workers_degrades_gracefully_when_redis_down(client, auth_headers, monkeypatch):
    from app.api.v1.endpoints import jobs as jobs_ep

    monkeypatch.setattr(
        jobs_ep,
        "recover_stuck_jobs",
        lambda: {
            "available": False,
            "error": "down",
            "requeued": 0,
            "cleared_failed": 0,
            "note": queue.WORKER_PROCESS_RESET_HINT,
        },
    )
    resp = client.post("/api/v1/jobs/reset-workers", headers=auth_headers("admin"))
    assert resp.status_code == 200  # never 500
    assert resp.json()["available"] is False


# --- queue helpers degrade gracefully without Redis -------------------------


def test_empty_queue_reports_unavailable_without_redis(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6390/0")
    get_settings.cache_clear()
    try:
        result = queue.empty_queue()
        assert result["available"] is False
        assert result["dropped"] == 0
    finally:
        get_settings.cache_clear()


def test_recover_stuck_jobs_reports_unavailable_without_redis(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6390/0")
    get_settings.cache_clear()
    try:
        result = queue.recover_stuck_jobs()
        assert result["available"] is False
        assert result["requeued"] == 0
        assert "docker compose restart worker" in result["note"]
    finally:
        get_settings.cache_clear()
