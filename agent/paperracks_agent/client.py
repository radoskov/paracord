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
