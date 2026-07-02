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
