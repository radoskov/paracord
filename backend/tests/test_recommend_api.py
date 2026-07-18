"""AI recommendation endpoints — create/cache/recompute/requester-gating/validation.

The background job is not exercised here (no worker); ``enqueue_recommend`` is stubbed so the run
stays ``running`` and the caching path is deterministic. The compute itself is covered by
test_recommendation.py."""

import pytest
from app.models.work import Work


@pytest.fixture()
def _stub_enqueue(monkeypatch):
    monkeypatch.setattr(
        "app.api.v1.endpoints.recommend.enqueue_recommend", lambda *a, **k: "job-stub-1"
    )


def test_create_is_cached_and_recompute_forces_new(client, auth_headers, db, _stub_enqueue) -> None:
    db.add(Work(canonical_title="A", normalized_title="a"))
    db.add(Work(canonical_title="B", normalized_title="b"))
    db.commit()
    owner = auth_headers("owner")  # reuse this dict → same user across calls
    body = {"scope_type": "library", "mode": "categorization", "k": 3}

    first = client.post("/api/v1/recommend", headers=owner, json=body)
    assert first.status_code == 200
    r1 = first.json()
    assert r1["status"] == "running" and r1["job_id"] == "job-stub-1"
    assert r1["params"]["work_ids"] and r1["params"]["k"] == 3

    # Same settings + same user → the cached run (no new row, no new job).
    again = client.post("/api/v1/recommend", headers=owner, json=body).json()
    assert again["id"] == r1["id"] and again["job_id"] is None

    # recompute → a fresh run.
    forced = client.post(
        "/api/v1/recommend", headers=owner, json={**body, "recompute": True}
    ).json()
    assert forced["id"] != r1["id"]

    # Different settings (k) → a fresh run too.
    other_k = client.post("/api/v1/recommend", headers=owner, json={**body, "k": 5}).json()
    assert other_k["id"] != r1["id"]


def test_get_is_requester_gated(client, auth_headers, db, _stub_enqueue) -> None:
    db.add(Work(canonical_title="A", normalized_title="a"))
    db.commit()
    owner = auth_headers("owner")
    run_id = client.post(
        "/api/v1/recommend", headers=owner, json={"scope_type": "library", "mode": "tags", "k": 3}
    ).json()["id"]

    assert client.get(f"/api/v1/recommend/{run_id}", headers=owner).status_code == 200
    # A different non-admin user (contributor+, so past the endpoint's role floor) can't read
    # someone else's run — 404, not a leak.
    stranger = auth_headers("editor")
    assert client.get(f"/api/v1/recommend/{run_id}", headers=stranger).status_code == 404


def test_validation_rejects_bad_settings(client, auth_headers, db, _stub_enqueue) -> None:
    owner = auth_headers("owner")
    assert (
        client.post(
            "/api/v1/recommend", headers=owner, json={"scope_type": "library", "mode": "bogus"}
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/v1/recommend", headers=owner, json={"scope_type": "library", "scoring": "bogus"}
        ).status_code
        == 400
    )
    assert (
        client.post("/api/v1/recommend", headers=owner, json={"scope_type": "nope"}).status_code
        == 400
    )
