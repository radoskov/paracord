"""End-to-end acceptance for the agent manifest + teleport vertical (M5).

Exercises the real, secure flow through the HTTP API: owner enrolls + approves an agent, the agent
reports a manifest, a user requests a teleport, the agent pushes the bytes, and the server verifies
the hash and stores a managed file. The server never sees a path on the agent's machine; the agent
never accepts a path from the server.

Marked ``slow`` because it is a large multi-step round trip (enroll → approve → manifest → request →
push → verify) rather than because of the feature's maturity — it runs in `make test-full`/`make
ready-full`, CI, and `pytest -m slow`, but not in the fast `make test`/`make ready` tier.
"""

from __future__ import annotations

import hashlib
import io
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.slow

_PDF = b"%PDF-1.4\n%%EOF\n"
_SHA = hashlib.sha256(_PDF).hexdigest()
_PREVIEW = {"page_count": 1, "preview_text": "x", "text_layer_quality": "unknown"}


def test_agent_manifest_to_server_to_teleport_round_trip(client, auth_headers) -> None:
    owner = auth_headers("owner")

    # 1. Owner mints an enrollment token; agent enrolls; owner approves -> agent token.
    enroll_token = client.post("/api/v1/admin/agents/enroll-token", headers=owner).json()["token"]
    agent_id = client.post(
        "/api/v1/agents/enroll-request",
        json={"token": enroll_token, "name": "workstation"},
    ).json()["agent_id"]
    approval = client.post(f"/api/v1/admin/agents/{agent_id}/approve", headers=owner)
    assert approval.status_code == 200
    agent = {"Authorization": f"Bearer {approval.json()['agent_token']}"}
    # Teleport is opt-in (can_teleport defaults off); grant it.
    client.patch(
        f"/api/v1/admin/agents/{agent_id}/privileges", headers=owner, json={"can_teleport": True}
    )

    # 2. Agent reports a manifest (opaque identity only — no server-usable path).
    manifest = client.post(
        "/api/v1/agents/manifest",
        headers=agent,
        json={
            "items": [
                {
                    "local_file_id": "local-1",
                    "sha256": _SHA,
                    "size_bytes": len(_PDF),
                    "display_path": "papers/attention.pdf",
                    "mime_type": "application/pdf",
                }
            ]
        },
    )
    assert manifest.status_code == 202

    # 3. A user requests the teleport; the agent sees it pending.
    requested = client.post(
        "/api/v1/imports/teleport",
        headers=owner,
        json={"agent_id": agent_id, "local_file_id": "local-1"},
    )
    assert requested.status_code == 202
    pending = client.get("/api/v1/agents/teleports/pending", headers=agent).json()
    assert [item["local_file_id"] for item in pending] == ["local-1"]

    # 4. The agent pushes the bytes; the server verifies the hash and stores the managed file.
    with patch("app.services.storage._extract_pdf_preview", return_value=_PREVIEW):
        content = client.post(
            "/api/v1/agents/teleports/local-1/content",
            headers=agent,
            files={"file": ("attention.pdf", io.BytesIO(_PDF), "application/pdf")},
        )
    assert content.status_code == 201

    # 5. The teleported file is now a managed-library file.
    files = client.get("/api/v1/files", headers=owner).json()
    assert any(file["sha256"] == _SHA for file in files)


def test_teleport_rejects_hash_mismatch(client, auth_headers) -> None:
    owner = auth_headers("owner")
    enroll_token = client.post("/api/v1/admin/agents/enroll-token", headers=owner).json()["token"]
    agent_id = client.post(
        "/api/v1/agents/enroll-request", json={"token": enroll_token, "name": "ws"}
    ).json()["agent_id"]
    agent = {
        "Authorization": "Bearer "
        + client.post(f"/api/v1/admin/agents/{agent_id}/approve", headers=owner).json()[
            "agent_token"
        ]
    }
    client.patch(
        f"/api/v1/admin/agents/{agent_id}/privileges", headers=owner, json={"can_teleport": True}
    )
    client.post(
        "/api/v1/agents/manifest",
        headers=agent,
        json={"items": [{"local_file_id": "x", "sha256": "b" * 64, "size_bytes": 10}]},
    )
    client.post(
        "/api/v1/imports/teleport",
        headers=owner,
        json={"agent_id": agent_id, "local_file_id": "x"},
    )
    # The manifest claims sha b*64 but the bytes hash to something else -> rejected.
    rejected = client.post(
        "/api/v1/agents/teleports/x/content",
        headers=agent,
        files={"file": ("x.pdf", io.BytesIO(_PDF), "application/pdf")},
    )
    assert rejected.status_code == 400
