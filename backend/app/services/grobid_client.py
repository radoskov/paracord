"""Client for the GROBID REST service."""

from pathlib import Path

import httpx2 as httpx

from app.core.config import Settings, get_settings


class GrobidUnavailableError(RuntimeError):
    """GROBID could not be reached — almost always because the service isn't running."""


def _unavailable(base_url: str, exc: Exception) -> GrobidUnavailableError:
    return GrobidUnavailableError(
        f"GROBID is unreachable at {base_url} ({exc.__class__.__name__}). "
        "The extraction service is not part of the default stack — start it with "
        "`make up-extraction` (or `docker compose --profile extraction up -d grobid`) "
        "and confirm GROBID_URL points at it."
    )


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

    def _form_data(self) -> dict[str, str | list[str]]:
        """Build the multipart form fields for processFulltextDocument.

        Returned as a dict; ``teiCoordinates`` is a **list** so httpx emits one repeated part per
        element (the shape GROBID expects for a multi-element coordinate request). A list of
        ``(key, value)`` tuples is *not* used here — httpx2's multipart encoder mishandles it.
        """
        settings = self._settings
        data: dict[str, str | list[str]] = {
            "consolidateHeader": "1" if settings.grobid_consolidate_header else "0",
            "consolidateCitations": "1" if settings.grobid_consolidate_citations else "0",
            "includeRawCitations": "1" if settings.grobid_include_raw_citations else "0",
            "segmentSentences": "1" if settings.grobid_segment_sentences else "0",
        }
        if settings.grobid_coordinate_elements:
            data["teiCoordinates"] = list(settings.grobid_coordinate_elements)
        return data

    async def process_fulltext_document(self, pdf_path: Path) -> str:
        """Extract TEI XML from a PDF."""
        try:
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
        except httpx.ConnectError as exc:
            raise _unavailable(self.base_url, exc) from exc

    def process_fulltext_document_sync(self, pdf_path: Path) -> str:
        """Synchronous TEI extraction for use inside RQ workers."""
        try:
            with httpx.Client(timeout=120) as client, pdf_path.open("rb") as handle:
                files = {"input": (pdf_path.name, handle, "application/pdf")}
                response = client.post(
                    f"{self.base_url}/api/processFulltextDocument",
                    files=files,
                    data=self._form_data(),
                )
            response.raise_for_status()
            return response.text
        except httpx.ConnectError as exc:
            raise _unavailable(self.base_url, exc) from exc
