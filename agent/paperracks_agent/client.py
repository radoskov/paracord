"""HTTP client for server communication."""

import httpx2 as httpx


class PaRacORDServerClient:
    """Agent-side API client."""

    def __init__(self, server_url: str, token: str | None = None) -> None:
        self.server_url = server_url.rstrip("/")
        self.token = token

    def _headers(self) -> dict[str, str]:
        if not self.token:
            return {}
        return {"Authorization": f"Bearer {self.token}"}

    async def enroll(self, enrollment_token: str, name: str) -> dict:
        """Enroll with an owner-issued enrollment token (creates a pending agent)."""
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self.server_url}/api/v1/agents/enroll-request",
                json={"token": enrollment_token, "name": name},
            )
            response.raise_for_status()
            return response.json()

    async def send_manifest(self, payload: dict) -> None:
        """Send a file manifest to the server."""
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self.server_url}/api/v1/agents/manifest",
                json=payload,
                headers=self._headers(),
            )
            response.raise_for_status()

    async def get_pending_teleports(self) -> list[dict]:
        """Return the files a user has requested this agent to teleport (by local_file_id)."""
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{self.server_url}/api/v1/agents/teleports/pending",
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.json()

    async def upload_teleport_content(self, local_file_id: str, handle) -> dict:
        """Push the bytes for a requested teleport; the server verifies the hash before storing."""
        async with httpx.AsyncClient(timeout=120) as client:
            files = {"file": (f"{local_file_id}.pdf", handle, "application/pdf")}
            response = await client.post(
                f"{self.server_url}/api/v1/agents/teleports/{local_file_id}/content",
                files=files,
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.json()

    async def upload_for_extraction(self, local_file_id: str, handle) -> dict:
        """Push bytes for index_and_extract: the server extracts then discards the PDF."""
        async with httpx.AsyncClient(timeout=120) as client:
            files = {"file": (f"{local_file_id}.pdf", handle, "application/pdf")}
            response = await client.post(
                f"{self.server_url}/api/v1/agents/files/{local_file_id}/extract",
                files=files,
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.json()

    async def reject_teleport(self, local_file_id: str, forever: bool = False) -> dict:
        """Reject a pending teleport request (optionally block all future requests)."""
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self.server_url}/api/v1/agents/teleports/{local_file_id}/reject",
                json={"forever": forever},
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.json()

    async def unblock_teleport(self, local_file_id: str) -> dict:
        """Clear a reject-forever block."""
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self.server_url}/api/v1/agents/teleports/{local_file_id}/unblock",
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.json()

    async def get_me(self) -> dict:
        """Return this agent's identity + privileges (also a reachability/auth check)."""
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{self.server_url}/api/v1/agents/me", headers=self._headers()
            )
            response.raise_for_status()
            return response.json()

    async def get_my_files(self) -> list[dict]:
        """Return this agent's files + their server-side processing/teleport state."""
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{self.server_url}/api/v1/agents/files", headers=self._headers()
            )
            response.raise_for_status()
            return response.json()

    async def report_source_removed(self, local_file_ids: list[str]) -> dict:
        """Tell the server which files disappeared on the client (kept + flagged)."""
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self.server_url}/api/v1/agents/files/source-removed",
                json={"local_file_ids": local_file_ids},
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.json()
