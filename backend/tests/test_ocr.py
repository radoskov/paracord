"""OCR service tests (Phase B5). No real OCR runs — subprocess + shutil.which are mocked."""

import subprocess
from pathlib import Path

import pytest
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


def test_ml_extraction_unavailable_gives_clear_error(tmp_path: Path, monkeypatch) -> None:
    # find_spec returns None → selected-but-absent → a clear install-path error, no torch import.
    monkeypatch.setattr("importlib.util.find_spec", lambda _name: None)
    assert ocr_service.ml_extraction_available("nougat") is False
    with pytest.raises(RuntimeError, match="make build-ml-extraction"):
        ocr_service.run_ml_extraction(tmp_path / "in.pdf", backend="nougat")


def test_ml_extraction_unknown_backend() -> None:
    assert ocr_service.ml_extraction_available("bogus") is False
    with pytest.raises(ValueError, match="Unknown ML extraction backend"):
        ocr_service.run_ml_extraction(Path("x.pdf"), backend="bogus")
