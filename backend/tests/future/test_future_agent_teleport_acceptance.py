"""Future acceptance tests for the real agent manifest + teleport vertical.

These are intentionally skipped at the current stage. They document the target
behavior so later agents can unskip them when the feature is implemented.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(reason="future stage: real agent manifest/teleport vertical")


def test_agent_manifest_to_server_to_teleport_round_trip(client, auth_headers, tmp_path) -> None:
    owner_headers = auth_headers("owner")

    enroll = client.post("/api/v1/admin/agents/enroll-token", headers=owner_headers)
    assert enroll.status_code == 201
    enroll_token = enroll.json()["token"]

    agent_pdf = tmp_path / "workstation" / "paper.pdf"
    agent_pdf.parent.mkdir()
    agent_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")

    register = client.post(
        "/api/v1/agents/enroll",
        json={"name": "workstation", "enrollment_token": enroll_token},
    )
    assert register.status_code == 201
    agent_id = register.json()["agent_id"]

    approved = client.post(f"/api/v1/admin/agents/{agent_id}/approve", headers=owner_headers)
    assert approved.status_code == 200
    agent_headers = {"Authorization": f"Bearer {approved.json()['agent_token']}"}

    manifest = client.post(
        "/api/v1/agents/manifest",
        headers=agent_headers,
        json={
            "items": [
                {
                    "local_file_id": "local-1",
                    "sha256": "a" * 64,
                    "size_bytes": agent_pdf.stat().st_size,
                    "display_path": "paper.pdf",
                    "mime_type": "application/pdf",
                }
            ]
        },
    )
    assert manifest.status_code == 202

    teleport = client.post(
        "/api/v1/imports/teleport",
        headers=owner_headers,
        json={"agent_id": agent_id, "local_file_id": "local-1"},
    )
    assert teleport.status_code == 201

    files = client.get("/api/v1/files", headers=owner_headers).json()
    assert any(file["sha256"] == "a" * 64 for file in files)
