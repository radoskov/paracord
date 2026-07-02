"""OCR service tests (Phase B5). No real OCR runs — subprocess + shutil.which are mocked."""

import subprocess
from pathlib import Path

from app.services import ocr as ocr_service


def _fake_run_ok(captured: dict):
    def run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        # Simulate ocrmypdf writing the output file so run_ocrmypdf returns a real path.
        Path(cmd[-1]).write_bytes(b"%PDF-1.4\n%%EOF\n")
        return subprocess.CompletedProcess(cmd, 0)

    return run


def test_run_ocrmypdf_builds_skip_text_command(tmp_path: Path) -> None:
    captured: dict = {}
    pdf = tmp_path / "in.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    out = ocr_service.run_ocrmypdf(
        pdf, out_dir=tmp_path / "scratch", timeout=123, language="deu", run=_fake_run_ok(captured)
    )
    cmd = captured["cmd"]
    assert cmd[0] == "ocrmypdf"
    assert "--skip-text" in cmd  # add text layer only where missing
    assert "--force-ocr" not in cmd and "--redo-ocr" not in cmd  # never re-OCR digital pages
    assert "--language" in cmd and "deu" in cmd
    assert captured["kwargs"]["timeout"] == 123
    assert captured["kwargs"]["check"] is True
    assert out.exists()


def test_needs_ocr_truth_table() -> None:
    assert ocr_service.needs_ocr(None) is True
    assert ocr_service.needs_ocr("unknown") is True
    assert ocr_service.needs_ocr("poor") is True
    assert ocr_service.needs_ocr("none") is True
    assert ocr_service.needs_ocr("good") is False
    assert ocr_service.needs_ocr("ocr_added") is False


def test_maybe_ocr_skips_when_text_layer_good(tmp_path: Path) -> None:
    pdf = tmp_path / "in.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    def run(*_a, **_k):  # pragma: no cover - must not be called
        raise AssertionError("OCR should not run for a good text layer")

    result = ocr_service.maybe_ocr(pdf, text_layer_quality="good", out_dir=tmp_path / "s", run=run)
    assert result.ran is False
    assert result.output_pdf_path == pdf
    assert result.engine is None


def test_maybe_ocr_skips_when_cli_absent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ocr_service, "ocrmypdf_available", lambda: False)
    pdf = tmp_path / "in.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    result = ocr_service.maybe_ocr(pdf, text_layer_quality="poor", out_dir=tmp_path / "s")
    assert result.ran is False
    assert result.output_pdf_path == pdf
    assert result.error == "ocrmypdf not installed"


def test_maybe_ocr_runs_and_marks_ocr_added(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ocr_service, "ocrmypdf_available", lambda: True)
    captured: dict = {}
    pdf = tmp_path / "in.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    result = ocr_service.maybe_ocr(
        pdf, text_layer_quality="poor", out_dir=tmp_path / "s", run=_fake_run_ok(captured)
    )
    assert result.ran is True
    assert result.engine == "ocrmypdf"
    assert result.text_layer_quality == "ocr_added"
    assert result.output_pdf_path != pdf
    assert result.output_pdf_path.exists()


def test_maybe_ocr_swallows_called_process_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ocr_service, "ocrmypdf_available", lambda: True)

    def run(cmd, **_k):
        raise subprocess.CalledProcessError(4, cmd, stderr=b"boom")

    pdf = tmp_path / "in.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    result = ocr_service.maybe_ocr(pdf, text_layer_quality="poor", out_dir=tmp_path / "s", run=run)
    assert result.ran is False
    assert result.output_pdf_path == pdf  # falls back to the original PDF
    assert result.error is not None


def test_maybe_ocr_swallows_timeout(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ocr_service, "ocrmypdf_available", lambda: True)

    def run(cmd, **_k):
        raise subprocess.TimeoutExpired(cmd, 300)

    pdf = tmp_path / "in.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    result = ocr_service.maybe_ocr(pdf, text_layer_quality="poor", out_dir=tmp_path / "s", run=run)
    assert result.ran is False
    assert result.output_pdf_path == pdf
    assert result.error is not None


# --- PyMuPDF OCR backend ---


def test_pymupdf_available_reflects_fitz_and_tesseract(monkeypatch) -> None:
    # Absent tesseract → unavailable even if fitz imports.
    monkeypatch.setattr(ocr_service.shutil, "which", lambda _name: None)
    assert ocr_service.pymupdf_available() is False


def test_pymupdf_ocr_graceful_when_tessdata_missing(tmp_path: Path, monkeypatch) -> None:
    # No tessdata dir found → swallow the failure and return the ORIGINAL pdf (never raises).
    monkeypatch.setattr(ocr_service, "_tessdata_prefix", lambda: None)
    pdf = tmp_path / "in.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    result = ocr_service.pymupdf_ocr(pdf, out_dir=tmp_path / "s", language="eng")
    assert result.ran is False
    assert result.engine == "pymupdf"
    assert result.output_pdf_path == pdf  # falls back to the original PDF
    assert result.error is not None


def test_pymupdf_ocr_adds_searchable_text_layer(tmp_path: Path) -> None:
    """Real OCR: rasterise a text PDF, OCR it back to a searchable copy (needs fitz + tesseract)."""
    if not ocr_service.pymupdf_available():
        import pytest as _pytest

        _pytest.skip("PyMuPDF / tesseract not available in this environment")
    import fitz  # type: ignore[import-not-found]

    src = tmp_path / "src.pdf"
    doc = fitz.open()
    page = doc.new_page()
    # Large text so the rasterised page OCRs cleanly.
    page.insert_text((72, 200), "HELLO", fontsize=96)
    doc.save(str(src))
    doc.close()

    result = ocr_service.pymupdf_ocr(src, out_dir=tmp_path / "out", language="eng", dpi=200)
    assert result.ran is True
    assert result.engine == "pymupdf"
    assert result.text_layer_quality == "ocr_added"
    assert result.output_pdf_path.exists()
    with fitz.open(result.output_pdf_path) as out:
        text = "".join(p.get_text() for p in out)
    assert "HELLO" in text.upper()


def test_pymupdf_extract_text_native_and_run_ml_extraction(tmp_path: Path) -> None:
    """The PyMuPDF hard extractor reads the native text layer; run_ml_extraction wraps it."""
    if not ocr_service.pymupdf_available():
        import pytest as _pytest

        _pytest.skip("PyMuPDF / tesseract not available in this environment")
    import fitz  # type: ignore[import-not-found]

    src = tmp_path / "native.pdf"
    doc = fitz.open()
    page = doc.new_page()
    for i in range(10):
        page.insert_text((72, 90 + i * 18), "machine learning neural networks study", fontsize=11)
    doc.save(str(src))
    doc.close()

    text, source = ocr_service.pymupdf_extract_text(src, language="eng")
    assert source == "native"
    assert "neural networks" in text.lower()


def test_pymupdf_extract_text_graceful_on_bad_pdf(tmp_path: Path) -> None:
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    text, source = ocr_service.pymupdf_extract_text(bad, language="eng")
    assert text == "" and source == "none"
