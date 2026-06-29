"""Client for the GROBID REST service."""

from pathlib import Path

import httpx2 as httpx

from app.core.config import Settings, get_settings


class GrobidClient:
    """Minimal GROBID client wrapper.

    Extraction options (consolidation, raw citations, sentence segmentation, and which TEI
    elements get PDF coordinates) are driven from settings rather than hardcoded, so an
    operator can tune privacy/egress (consolidation calls external services) and enable the
    coordinate data the PDF reader anchors to.
    """

    def __init__(self, base_url: str, *, settings: Settings | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._settings = settings or get_settings()

    async def is_alive(self) -> bool:
        """Return whether GROBID responds to its liveness endpoint."""
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{self.base_url}/api/isalive")
            return response.status_code == 200

    def _form_data(self) -> list[tuple[str, str]]:
        """Build the multipart form fields for processFulltextDocument.

        Returned as a list of pairs so ``teiCoordinates`` can repeat once per element — the
        shape the GROBID REST API expects for a multi-element coordinate request.
        """
        settings = self._settings
        data: list[tuple[str, str]] = [
            ("consolidateHeader", "1" if settings.grobid_consolidate_header else "0"),
            ("consolidateCitations", "1" if settings.grobid_consolidate_citations else "0"),
            ("includeRawCitations", "1" if settings.grobid_include_raw_citations else "0"),
            ("segmentSentences", "1" if settings.grobid_segment_sentences else "0"),
        ]
        data.extend(("teiCoordinates", element) for element in settings.grobid_coordinate_elements)
        return data

    async def process_fulltext_document(self, pdf_path: Path) -> str:
        """Extract TEI XML from a PDF."""
        async with httpx.AsyncClient(timeout=120) as client:
            with pdf_path.open("rb") as handle:
                files = {"input": (pdf_path.name, handle, "application/pdf")}
                response = await client.post(
                    f"{self.base_url}/api/processFulltextDocument",
                    files=files,
                    data=self._form_data(),
                )
            response.raise_for_status()
            return response.text

    def process_fulltext_document_sync(self, pdf_path: Path) -> str:
        """Synchronous TEI extraction for use inside RQ workers."""
        with httpx.Client(timeout=120) as client, pdf_path.open("rb") as handle:
            files = {"input": (pdf_path.name, handle, "application/pdf")}
            response = client.post(
                f"{self.base_url}/api/processFulltextDocument",
                files=files,
                data=self._form_data(),
            )
        response.raise_for_status()
        return response.text
