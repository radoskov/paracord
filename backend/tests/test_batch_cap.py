"""Client import-batch cap (D1 overload protection).

The admin-editable ``max_batch_items`` ceiling rejects oversized client batches with 413; server
folder scans are exempt (covered elsewhere). These tests drive the HTTP layer so the AppConfig
round-trip and the 413 exception handler are exercised together.
"""

from __future__ import annotations

_THREE_ENTRIES = """
@article{a2020, title = {Alpha}, author = {A, X}, year = {2020}}
@article{b2021, title = {Beta}, author = {B, Y}, year = {2021}}
@article{c2022, title = {Gamma}, author = {C, Z}, year = {2022}}
"""

_TWO_ENTRIES = """
@article{a2020, title = {Alpha}, author = {A, X}, year = {2020}}
@article{b2021, title = {Beta}, author = {B, Y}, year = {2021}}
"""


def test_app_config_max_batch_items_round_trip(client, auth_headers):
    admin = auth_headers("owner")
    assert client.get("/api/v1/admin/app-config", headers=admin).json()["max_batch_items"] == 100
    updated = client.patch("/api/v1/admin/app-config", headers=admin, json={"max_batch_items": 25})
    assert updated.status_code == 200
    assert updated.json()["max_batch_items"] == 25


def test_bibtex_over_cap_rejected_413(client, auth_headers):
    admin = auth_headers("owner")
    client.patch("/api/v1/admin/app-config", headers=admin, json={"max_batch_items": 2})
    resp = client.post(
        "/api/v1/imports/bibtex",
        headers=auth_headers("editor"),
        json={"content": _THREE_ENTRIES},
    )
    assert resp.status_code == 413
    assert "limit" in resp.json()["detail"]


def test_bibtex_at_cap_allowed(client, auth_headers):
    admin = auth_headers("owner")
    client.patch("/api/v1/admin/app-config", headers=admin, json={"max_batch_items": 2})
    resp = client.post(
        "/api/v1/imports/bibtex",
        headers=auth_headers("editor"),
        json={"content": _TWO_ENTRIES},
    )
    assert resp.status_code == 201


def test_agent_me_reports_batch_cap(client, auth_headers):
    """The agent learns the cap from /agents/me so it can chunk oversized scans."""
    owner = auth_headers("owner")
    client.patch("/api/v1/admin/app-config", headers=owner, json={"max_batch_items": 42})

    enroll_token = client.post("/api/v1/admin/agents/enroll-token", headers=owner).json()["token"]
    enrolled = client.post(
        "/api/v1/agents/enroll-request", json={"token": enroll_token, "name": "laptop"}
    )
    agent_id = enrolled.json()["agent_id"]
    agent_token = client.post(f"/api/v1/admin/agents/{agent_id}/approve", headers=owner).json()[
        "agent_token"
    ]

    me = client.get("/api/v1/agents/me", headers={"Authorization": f"Bearer {agent_token}"})
    assert me.status_code == 200
    assert me.json()["max_batch_items"] == 42
