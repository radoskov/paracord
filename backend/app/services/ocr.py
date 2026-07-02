"""OCR / advanced-extraction seam for the GROBID pipeline (Phase B5, SPEC §8.3).

A PDF whose text layer is poor/none/unknown is fed through OCRmyPDF **before** GROBID, so the
searchable copy yields a real TEI (abstract/keywords/references) rather than an empty one. Design
constraints:

* OCR is a bounded local subprocess (``ocrmypdf``) — no network egress; it operates on the stored
  PDF only. Failure is swallowed (logged + audited) so extraction never fails because OCR did.
* The OCR'd PDF is **transient**: it is written to a scratch temp dir and fed to GROBID; only the
  improved TEI/abstract/keywords + an updated ``text_layer_quality`` ("ocr_added") persist. We do
  not store it as a managed artifact (a different SHA would pollute content-addressed dedup).
* The heavy ML extractors (Nougat/Marker) are **activate-when-present**: gated behind an
  ``importlib`` spec check so CI never imports torch, and they are installed only via the opt-in
  ML-extraction image extra (``make build-ml-extraction``) — never at runtime from the web UI.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Candidate tessdata locations, in preference order. Tesseract's traineddata files live here; a
# PyMuPDF OCR call needs ``TESSDATA_PREFIX`` pointing at one of these (the dev image leaves it
# unset). ``$TESSDATA_PREFIX`` wins when already set by the environment.
_TESSDATA_CANDIDATES = (
    "/usr/share/tesseract-ocr/5/tessdata",
    "/usr/share/tesseract-ocr/4.00/tessdata",
    "/usr/share/tessdata",
)

# Text-layer qualities for which OCR is worthwhile. "good" is skipped; "ocr_added" means a prior
# OCR pass already ran (re-running ocrmypdf --skip-text is a cheap no-op, so it is safe either way,
# but we treat it as "no OCR needed" to keep the pipeline idempotent).
_NEEDS_OCR_QUALITIES = frozenset({"poor", "none", "unknown"})
# The map from a selected ML backend to the Python module whose presence activates it.
_ML_BACKEND_MODULES = {"nougat": "nougat", "marker": "marker"}


@dataclass
class OcrResult:
    """Outcome of a ``maybe_ocr`` attempt (provenance for the extraction summary/audit)."""

    output_pdf_path: Path  # the path to feed GROBID (the OCR'd copy, or the original on skip/fail)
    ran: bool  # whether the OCR engine actually produced a new searchable PDF
    engine: str | None  # engine used ("ocrmypdf" / "pymupdf") when it ran, else None
    text_layer_quality: str | None  # the new quality to persist ("ocr_added") when it ran
    error: str | None  # a short error string when OCR was attempted but failed (swallowed)


def ocrmypdf_available() -> bool:
    """True when the ``ocrmypdf`` CLI is on PATH (the base image installs it + tesseract/gs)."""
    return shutil.which("ocrmypdf") is not None


def needs_ocr(quality: str | None) -> bool:
    """True when a file's ``text_layer_quality`` indicates OCR could help (poor/none/unknown/None)."""
    if quality is None:
        return True
    return quality in _NEEDS_OCR_QUALITIES


def run_ocrmypdf(
    pdf: Path,
    *,
    out_dir: Path,
    timeout: int = 300,
    language: str = "eng",
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> Path:
    """Run ``ocrmypdf --skip-text`` on ``pdf``, writing a searchable copy under ``out_dir``.

    ``--skip-text`` adds a text layer only to pages that lack one (never re-OCRs born-digital
    pages), so it is safe and idempotent. ``run`` is injected so tests can capture the argv and
    simulate timeout/failure without a real OCR run. Raises ``CalledProcessError`` /
    ``TimeoutExpired`` on failure — the caller (``maybe_ocr``) swallows those.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    output_pdf = out_dir / f"{pdf.stem}.ocr.pdf"
    cmd = [
        "ocrmypdf",
        "--skip-text",  # add a text layer only where missing; never force-OCR digital pages
        "--output-type",
        "pdf",
        "--optimize",
        "0",  # no lossy image optimisation — this copy is transient, fed straight to GROBID
        "--language",
        language,
        str(pdf),
        str(output_pdf),
    ]
    run(cmd, check=True, capture_output=True, timeout=timeout)
    return output_pdf


def maybe_ocr(
    pdf: Path,
    *,
    text_layer_quality: str | None,
    out_dir: Path,
    timeout: int = 300,
    language: str = "eng",
    skip_if_good: bool = True,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> OcrResult:
    """Orchestrate OCR for one PDF: never raises, returns a usable path even on skip/failure.

    Returns an :class:`OcrResult` whose ``output_pdf_path`` is the OCR'd copy when OCR ran, or the
    original ``pdf`` otherwise. Skipped (``ran=False``) when the text layer is already good, or when
    the ``ocrmypdf`` CLI is not installed. Any OCR failure is logged and captured in ``error`` —
    extraction proceeds on the original PDF.
    """
    if skip_if_good and not needs_ocr(text_layer_quality):
        return OcrResult(pdf, ran=False, engine=None, text_layer_quality=None, error=None)
    if not ocrmypdf_available():
        return OcrResult(
            pdf,
            ran=False,
            engine=None,
            text_layer_quality=None,
            error="ocrmypdf not installed",
        )
    try:
        output_pdf = run_ocrmypdf(pdf, out_dir=out_dir, timeout=timeout, language=language, run=run)
    except Exception as exc:  # noqa: BLE001 - OCR failure must NEVER fail extraction (SPEC §8.3)
        logger.warning("OCR (ocrmypdf) failed for %s: %s", pdf, exc)
        return OcrResult(pdf, ran=False, engine="ocrmypdf", text_layer_quality=None, error=str(exc))
    return OcrResult(
        output_pdf,
        ran=True,
        engine="ocrmypdf",
        text_layer_quality="ocr_added",
        error=None,
    )


def _tessdata_prefix() -> str | None:
    """Return the tessdata directory to point ``TESSDATA_PREFIX`` at (or None if none is found).

    Prefers an already-set ``$TESSDATA_PREFIX``; otherwise the first existing candidate. PyMuPDF's
    OCR calls read tesseract's traineddata from here, and the dev image leaves the var unset.
    """
    env = os.environ.get("TESSDATA_PREFIX")
    if env and Path(env).is_dir():
        return env
    for candidate in _TESSDATA_CANDIDATES:
        if Path(candidate).is_dir():
            return candidate
    return None


def pymupdf_available() -> bool:
    """True when PyMuPDF (``import fitz``) is importable AND tesseract is on PATH.

    PyMuPDF's OCR shells out to tesseract, so both must be present for ``pymupdf_ocr`` to work.
    """
    return importlib.util.find_spec("fitz") is not None and shutil.which("tesseract") is not None


def pymupdf_ocr(
    pdf: Path,
    *,
    out_dir: Path,
    language: str = "eng",
    dpi: int = 300,
    timeout: int | None = None,
) -> OcrResult:
    """OCR ``pdf`` with PyMuPDF + tesseract, writing a searchable copy under ``out_dir``.

    Each page is rasterised (``page.get_pixmap(dpi=dpi)``) and OCR'd to a single-page searchable
    PDF (``pix.pdfocr_tobytes(language=language)``); the pages are concatenated into one output PDF.
    ``language`` is passed through verbatim, so tesseract's multi-language syntax (``"eng+spa"``)
    works. Sets ``TESSDATA_PREFIX`` first (the dev image leaves it unset).

    Never raises — mirrors ``maybe_ocr``'s swallow-and-return contract: on any failure it returns an
    ``OcrResult`` whose ``output_pdf_path`` is the ORIGINAL ``pdf`` with ``ran=False`` and a short
    ``error``, so extraction proceeds on the original PDF. ``timeout`` (seconds), when given, is a
    best-effort wall-clock budget checked between pages.
    """
    try:
        import fitz  # type: ignore[import-not-found]  # noqa: PLC0415 (optional heavy dep)

        prefix = _tessdata_prefix()
        if prefix is None:
            raise RuntimeError("tessdata directory not found (set TESSDATA_PREFIX)")
        os.environ["TESSDATA_PREFIX"] = prefix

        out_dir.mkdir(parents=True, exist_ok=True)
        output_pdf = out_dir / f"{pdf.stem}.ocr.pdf"
        started = time.monotonic()
        with fitz.open(pdf) as document, fitz.open() as out:
            for page in document:
                if timeout is not None and time.monotonic() - started > timeout:
                    raise TimeoutError(f"pymupdf OCR exceeded {timeout}s")
                pixmap = page.get_pixmap(dpi=dpi)
                ocr_bytes = pixmap.pdfocr_tobytes(language=language)
                with fitz.open("pdf", ocr_bytes) as ocr_page:
                    out.insert_pdf(ocr_page)
            out.save(str(output_pdf))
        return OcrResult(
            output_pdf,
            ran=True,
            engine="pymupdf",
            text_layer_quality="ocr_added",
            error=None,
        )
    except Exception as exc:  # noqa: BLE001 - OCR failure must NEVER fail extraction (SPEC §8.3)
        logger.warning("OCR (pymupdf) failed for %s: %s", pdf, exc)
        return OcrResult(pdf, ran=False, engine="pymupdf", text_layer_quality=None, error=str(exc))


def ml_extraction_available(backend: str) -> bool:
    """True when the ML extractor for ``backend`` (nougat/marker) is importable in this image."""
    module = _ML_BACKEND_MODULES.get(backend)
    if module is None:
        return False
    return importlib.util.find_spec(module) is not None


def run_ml_extraction(pdf: Path, *, backend: str) -> str:
    """Run an opt-in ML extractor (Nougat/Marker), returning plain text/markdown for the paper.

    Gated behind an ``importlib`` spec check so torch/model deps are never imported unless the
    backend is both selected and actually installed. When selected-but-absent, raises a clear
    error naming the opt-in install path (the ML-extraction image extra / ``make
    build-ml-extraction``). The concrete model invocation lives behind the guard so CI — which does
    not install these — never imports torch.
    """
    module = _ML_BACKEND_MODULES.get(backend)
    if module is None:
        raise ValueError(f"Unknown ML extraction backend: {backend!r}")
    if importlib.util.find_spec(module) is None:
        raise RuntimeError(
            f"ML extraction backend {backend!r} is not installed in this image — install it via "
            f"the ML-extraction image extra (`make build-ml-extraction`)."
        )
    # Import + invocation are deliberately kept inside the guard (torch is multi-GB and never
    # imported in CI). The concrete PDF->text call is a follow-up once the opt-in image ships.
    raise NotImplementedError(
        f"ML extraction backend {backend!r} is installed but its extractor is not wired yet."
    )
