"""API-level end-to-end happy path (WORKPLAN_NEXT Stage 9).

Login → create papers → reindex + semantic search → styled export → paper.viewed audit, all over
HTTP through the app. A browser-level (Playwright) E2E is a separate follow-up; this guards the
core request flow against regressions.
"""

from app.core.security import hash_password
from app.models.user import User


def _login(client, db, username="e2e-owner", password="e2e-pass-12345"):  # pragma: allowlist secret
    db.add(User(username=username, password_hash=hash_password(password), role="owner"))
    db.commit()
    token = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_happy_path(client, db):
    headers = _login(client, db)

    # Create two papers.
    a = client.post(
        "/api/v1/works",
        headers=headers,
        json={"canonical_title": "Attention transformer model", "abstract": "attention mechanism"},
    ).json()
    b = client.post(
        "/api/v1/works",
        headers=headers,
        json={"canonical_title": "Sourdough bread baking", "abstract": "fermenting bread"},
    ).json()

    # They appear in the library list.
    works = client.get("/api/v1/works", headers=headers).json()["items"]
    titles = {w["canonical_title"] for w in works}
    assert {"Attention transformer model", "Sourdough bread baking"} <= titles

    # Build embeddings off the read path, then semantic search ranks the relevant paper first.
    assert client.post("/api/v1/search/reindex", headers=headers).status_code == 200
    hits = client.post(
        "/api/v1/search/semantic", headers=headers, json={"q": "transformer attention"}
    ).json()["items"]
    assert hits and hits[0]["title"] == "Attention transformer model"

    # Lexical mode also works without embeddings.
    lex = client.post(
        "/api/v1/search/semantic",
        headers=headers,
        json={"q": "bread baking", "mode": "lexical"},
    ).json()["items"]
    assert lex and lex[0]["title"] == "Sourdough bread baking"

    # Styled export of a selection.
    export = client.post(
        "/api/v1/exports",
        headers=headers,
        json={
            "scope_type": "selection",
            "work_ids": [a["id"], b["id"]],
            "format": "styled",
            "style": "ieee",
        },
    )
    assert export.status_code == 200
    content = export.json()["content"]
    assert "Attention transformer model" in content
    assert content.lstrip().startswith("[1]")  # IEEE numbering

    # Viewing a paper records a paper.viewed audit event surfaced to the owner.
    client.get(f"/api/v1/works/{a['id']}", headers=headers)
    events = client.get("/api/v1/admin/audit-events?limit=50", headers=headers).json()["items"]
    assert any(e["event_type"] == "paper.viewed" for e in events)
