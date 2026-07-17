"""GROBID client behaviour — notably the actionable error when the service isn't running."""

import httpx2 as httpx
import pytest
from app.services import grobid_client as gc


class _ConnectErrorClient:
    """Stand-in httpx.Client whose POST always fails to connect (service down / no DNS)."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def post(self, *args, **kwargs):
        raise httpx.ConnectError("[Errno -3] Temporary failure in name resolution")


class _TimeoutClient(_ConnectErrorClient):
    """Stand-in httpx.Client whose POST always times out (hung/overloaded service)."""

    def post(self, *args, **kwargs):
        raise httpx.ReadTimeout("timed out")


def test_sync_extraction_raises_actionable_error_when_grobid_down(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(gc.httpx, "Client", _ConnectErrorClient)
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    with pytest.raises(gc.GrobidUnavailableError) as excinfo:
        gc.GrobidClient("http://grobid:8070").process_fulltext_document_sync(pdf)

    message = str(excinfo.value)
    assert "http://grobid:8070" in message
    assert "up-extraction" in message  # points the operator at the fix


def test_sync_extraction_maps_timeout_to_unavailable(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(gc.httpx, "Client", _TimeoutClient)
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    with pytest.raises(gc.GrobidUnavailableError):
        gc.GrobidClient("http://grobid:8070").process_fulltext_document_sync(pdf)


# ------------------------------------------------ degraded header+references fallback (500s)

HEADER_TEI = (
    '<TEI xmlns="http://www.tei-c.org/ns/1.0"><teiHeader>H</teiHeader>'
    '<text xml:lang="en"></text></TEI>'
)
REFS_TEI = (
    '<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><back>'
    "<listBibl><biblStruct>R1</biblStruct></listBibl>"
    "</back></text></TEI>"
)


def test_merge_splices_listbibl_into_header_text() -> None:
    merged = gc.merge_header_and_references(HEADER_TEI, REFS_TEI)
    assert "<listBibl><biblStruct>R1</biblStruct></listBibl>" in merged
    assert merged.index("<listBibl>") < merged.index("</text>")


def test_merge_without_references_keeps_header_and_stamps_marker() -> None:
    merged = gc.merge_header_and_references(HEADER_TEI, None)
    assert merged.replace(gc.DEGRADED_TEI_MARKER, "") == HEADER_TEI
    assert gc.is_degraded_tei(merged)
    assert not gc.is_degraded_tei(HEADER_TEI)


def test_merge_injects_plain_text_body_pages_escaped() -> None:
    merged = gc.merge_header_and_references(
        HEADER_TEI, REFS_TEI, body_text_pages=["Page one.", "a < b & c"]
    )
    assert "<head>Full text</head>" in merged
    assert "<p>Page one.</p>" in merged
    assert "<p>a &lt; b &amp; c</p>" in merged  # XML-escaped
    assert merged.index("<body>") < merged.index("<back>")  # body precedes back within <text>


class _CrashyFulltextClient:
    """Stub httpx.Client: full-text 500s (GROBID-internal bug); header + references succeed."""

    calls: list[str] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def post(self, url, **kwargs):
        _CrashyFulltextClient.calls.append(url)
        request = httpx.Request("POST", url)
        if url.endswith("processFulltextDocument"):
            return httpx.Response(500, text="[GENERAL] An exception occurred", request=request)
        if url.endswith("processHeaderDocument"):
            return httpx.Response(200, text=HEADER_TEI, request=request)
        if url.endswith("processReferences"):
            return httpx.Response(200, text=REFS_TEI, request=request)
        raise AssertionError(f"unexpected URL {url}")


class _EverythingCrashesClient(_CrashyFulltextClient):
    """Stub httpx.Client where the header fallback fails too — the original error surfaces."""

    def post(self, url, **kwargs):
        request = httpx.Request("POST", url)
        return httpx.Response(500, text="[GENERAL] An exception occurred", request=request)


def test_fulltext_500_degrades_to_header_plus_references(tmp_path, monkeypatch) -> None:
    """The open_ease.pdf case: GROBID's body formatter crashes but header/references parse."""
    _CrashyFulltextClient.calls = []
    monkeypatch.setattr(gc.httpx, "Client", _CrashyFulltextClient)
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    tei = gc.GrobidClient("http://grobid:8070").process_fulltext_document_sync(pdf)
    assert "<listBibl>" in tei and "<teiHeader>H</teiHeader>" in tei
    assert [u.rsplit("/", 1)[-1] for u in _CrashyFulltextClient.calls] == [
        "processFulltextDocument",
        "processHeaderDocument",
        "processReferences",
    ]


def test_degraded_path_raises_original_error_when_header_also_fails(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(gc.httpx, "Client", _EverythingCrashesClient)
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        gc.GrobidClient("http://grobid:8070").process_fulltext_document_sync(pdf)
    assert excinfo.value.response.status_code == 500
    assert "processFulltextDocument" in str(excinfo.value.request.url)
