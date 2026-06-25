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
