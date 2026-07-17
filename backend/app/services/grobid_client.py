"""Client for the GROBID REST service."""

import logging
import re
from pathlib import Path

import httpx2 as httpx

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


class GrobidUnavailableError(RuntimeError):
    """GROBID could not be reached — almost always because the service isn't running."""


_LISTBIBL_RE = re.compile(r"<listBibl>.*</listBibl>", re.DOTALL)


def merge_header_and_references(header_tei: str, references_tei: str | None) -> str:
    """Build a degraded-but-useful TEI from the header + references endpoints' outputs.

    Some PDFs crash GROBID's FULL-TEXT body formatter with an internal 500
    (``TEIFormatter.toTEITextPiece: fromIndex > toIndex``) while its header and references
    parsers handle the same file fine. Splice the references ``<listBibl>`` into the header TEI's
    ``<text>`` so the standard ``parse_tei`` sees title/authors/abstract/DOI AND the bibliography
    — only body sections (and their citation contexts) are lost. Both documents share the default
    TEI namespace, so a string splice keeps the fragment well-formed.
    """
    listbibl = None
    if references_tei:
        match = _LISTBIBL_RE.search(references_tei)
        if match:
            listbibl = match.group(0)
    if not listbibl:
        return header_tei
    fragment = f'<back><div type="references">{listbibl}</div></back>'
    if "</text>" in header_tei:
        return header_tei.replace("</text>", f"{fragment}</text>", 1)
    if "</TEI>" in header_tei:
        return header_tei.replace("</TEI>", f"<text>{fragment}</text></TEI>", 1)
    return header_tei


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
        """Extract TEI XML from a PDF (degrading to header+references on a GROBID-internal 500)."""
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                with pdf_path.open("rb") as handle:
                    files = {"input": (pdf_path.name, handle, "application/pdf")}
                    response = await client.post(
                        f"{self.base_url}/api/processFulltextDocument",
                        files=files,
                        data=self._form_data(),
                    )
                if response.status_code >= 500:
                    return await self._degraded_fulltext(client, pdf_path, response)
                response.raise_for_status()
                return response.text
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise _unavailable(self.base_url, exc) from exc

    async def _degraded_fulltext(
        self, client: "httpx.AsyncClient", pdf_path: Path, fulltext_response: "httpx.Response"
    ) -> str:
        """Async twin of the sync degraded path (see ``process_fulltext_document_sync``)."""
        logger.warning(
            "GROBID full-text failed with %s for %s — degrading to header+references",
            fulltext_response.status_code,
            pdf_path.name,
        )
        with pdf_path.open("rb") as handle:
            header = await client.post(
                f"{self.base_url}/api/processHeaderDocument",
                files={"input": (pdf_path.name, handle, "application/pdf")},
                headers={"Accept": "application/xml"},
                data={"consolidateHeader": self._form_data()["consolidateHeader"]},
            )
        if header.status_code != 200:
            fulltext_response.raise_for_status()  # degraded path is unusable → original error
        references_tei: str | None = None
        with pdf_path.open("rb") as handle:
            refs = await client.post(
                f"{self.base_url}/api/processReferences",
                files={"input": (pdf_path.name, handle, "application/pdf")},
                data={"includeRawCitations": self._form_data()["includeRawCitations"]},
            )
        if refs.status_code == 200:
            references_tei = refs.text
        return merge_header_and_references(header.text, references_tei)

    def process_fulltext_document_sync(self, pdf_path: Path) -> str:
        """Synchronous TEI extraction for use inside RQ workers.

        Degrades on a GROBID-internal 5xx from the full-text endpoint: some PDFs crash GROBID's
        body formatter while its header/references parsers handle them fine, so retry those two
        endpoints and merge — the paper still gets metadata + its bibliography, only body
        sections are lost. If even the header fails, the original full-text error is raised.
        """
        try:
            with httpx.Client(timeout=120) as client:
                with pdf_path.open("rb") as handle:
                    files = {"input": (pdf_path.name, handle, "application/pdf")}
                    response = client.post(
                        f"{self.base_url}/api/processFulltextDocument",
                        files=files,
                        data=self._form_data(),
                    )
                if response.status_code >= 500:
                    return self._degraded_fulltext_sync(client, pdf_path, response)
                response.raise_for_status()
                return response.text
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise _unavailable(self.base_url, exc) from exc

    def _degraded_fulltext_sync(
        self, client: "httpx.Client", pdf_path: Path, fulltext_response: "httpx.Response"
    ) -> str:
        """Header+references fallback for PDFs whose full-text processing 500s inside GROBID."""
        logger.warning(
            "GROBID full-text failed with %s for %s — degrading to header+references",
            fulltext_response.status_code,
            pdf_path.name,
        )
        with pdf_path.open("rb") as handle:
            header = client.post(
                f"{self.base_url}/api/processHeaderDocument",
                files={"input": (pdf_path.name, handle, "application/pdf")},
                headers={"Accept": "application/xml"},
                data={"consolidateHeader": self._form_data()["consolidateHeader"]},
            )
        if header.status_code != 200:
            fulltext_response.raise_for_status()  # degraded path is unusable → original error
        references_tei: str | None = None
        with pdf_path.open("rb") as handle:
            refs = client.post(
                f"{self.base_url}/api/processReferences",
                files={"input": (pdf_path.name, handle, "application/pdf")},
                data={"includeRawCitations": self._form_data()["includeRawCitations"]},
            )
        if refs.status_code == 200:
            references_tei = refs.text
        return merge_header_and_references(header.text, references_tei)

    def _citation_form_data(self, citations: str | list[str]) -> dict[str, str | list[str]]:
        """Build the form fields for the citation-parse endpoints.

        ``citations`` is sent as a (possibly repeated) ``citations`` field — a list yields one
        repeated part per raw string, which is the shape ``/api/processCitationList`` expects for a
        batch. ``consolidateCitations`` is driven from settings (same privacy/egress knob as the
        full-text path).
        """
        return {
            "citations": citations,
            "consolidateCitations": "1" if self._settings.grobid_consolidate_citations else "0",
            "includeRawCitations": "1" if self._settings.grobid_include_raw_citations else "0",
        }

    def process_citation_sync(self, raw_citation: str) -> str:
        """Parse a single raw citation string into TEI (``/api/processCitation``).

        Synchronous — intended for the batch-import request path (timeboxed, never inside the
        async event loop). Raises :class:`GrobidUnavailableError` when GROBID is unreachable.
        """
        try:
            with httpx.Client(timeout=60) as client:
                response = client.post(
                    f"{self.base_url}/api/processCitation",
                    data=self._citation_form_data(raw_citation),
                )
            response.raise_for_status()
            return response.text
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise _unavailable(self.base_url, exc) from exc

    def process_citation_list_sync(self, raw_citations: list[str]) -> str:
        """Parse many raw citation strings in ONE call (``/api/processCitationList``).

        The strings are sent as repeated ``citations`` form fields (preferred for batch — a single
        HTTP round-trip). Returns a TEI document whose ``listBibl`` holds one ``biblStruct`` per
        parsed citation. Raises :class:`GrobidUnavailableError` when GROBID is unreachable.
        """
        try:
            with httpx.Client(timeout=120) as client:
                response = client.post(
                    f"{self.base_url}/api/processCitationList",
                    data=self._citation_form_data(list(raw_citations)),
                )
            response.raise_for_status()
            return response.text
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise _unavailable(self.base_url, exc) from exc
