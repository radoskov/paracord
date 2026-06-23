"""Client for the GROBID REST service."""

from pathlib import Path

import httpx


class GrobidClient:
    """Minimal GROBID client wrapper."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    async def is_alive(self) -> bool:
        """Return whether GROBID responds to its liveness endpoint."""
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{self.base_url}/api/isalive")
            return response.status_code == 200

    async def process_fulltext_document(self, pdf_path: Path) -> str:
        """Extract TEI XML from a PDF.

        TODO: Add extraction options from config, including consolidation, coordinates, and sentence segmentation.
        """
        async with httpx.AsyncClient(timeout=120) as client:
            with pdf_path.open("rb") as handle:
                files = {"input": (pdf_path.name, handle, "application/pdf")}
                data = {
                    "consolidateHeader": "1",
                    "consolidateCitations": "1",
                    "includeRawCitations": "1",
                    "segmentSentences": "1",
                }
                response = await client.post(
                    f"{self.base_url}/api/processFulltextDocument",
                    files=files,
                    data=data,
                )
            response.raise_for_status()
            return response.text
