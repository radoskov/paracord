

def test_approve_with_ttl_mints_expiring_token(client, auth_headers, db) -> None:
    """D3: approval may set token_ttl_days; an expired token is rejected with 401."""
    from datetime import UTC, datetime, timedelta

    from app.models.agent import Agent

    agent = Agent(name="temp-user-agent", status="pending")
    db.add(agent)
    db.commit()
    resp = client.post(
        f"/api/v1/admin/agents/{agent.id}/approve",
        headers=auth_headers("owner"),
        json={"token_ttl_days": 7},
    )
    assert resp.status_code == 200
    token = resp.json()["agent_token"]
    db.refresh(agent)
    assert agent.token_expires_at is not None

    # Valid while inside the window …
    ok = client.post(
        "/api/v1/agents/manifest",
        headers={"Authorization": f"Bearer {token}"},
        json={"files": []},
    )
    assert ok.status_code != 401

    # … and rejected once expired.
    agent.token_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()
    expired = client.post(
        "/api/v1/agents/manifest",
        headers={"Authorization": f"Bearer {token}"},
        json={"files": []},
    )
    assert expired.status_code == 401
    assert "expired" in expired.json()["detail"].lower()


def test_approve_without_ttl_stays_permanent(client, auth_headers, db) -> None:
    from app.models.agent import Agent

    agent = Agent(name="owner-agent", status="pending")
    db.add(agent)
    db.commit()
    resp = client.post(
        f"/api/v1/admin/agents/{agent.id}/approve", headers=auth_headers("owner"), json={}
    )
    assert resp.status_code == 200
    db.refresh(agent)
    assert agent.token_expires_at is None
