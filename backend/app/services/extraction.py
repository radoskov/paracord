"""Persist GROBID extraction results as provenance-aware metadata and references.

Follows the spec rule that external sources never silently overwrite user-confirmed data:
every value is recorded as a MetadataAssertion, and canonical fields are only promoted when
the work has not been user-confirmed and the field is empty or filename-derived.
"""

import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.citation import CitationMention, RawTeiDocument, Reference, ReferenceCitation
from app.models.file import File, FileWorkLink
from app.models.metadata import MetadataAssertion
from app.models.work import Work
from app.services import ocr as ocr_service
from app.services.ai_config import get_ai_config
from app.services.app_config import effective_use_fuzzy_match_as_confirmed
from app.services.audit import record_event
from app.services.file_paths import resolve_backend_readable_pdf_path, save_derived_ocr_pdf
from app.services.keyword_extraction import extract_keywords
from app.services.reference_links import find_or_create_reference
from app.services.reference_matching import (
    rescan_references_for_new_work,
    run_matching_for_references,
)
from app.services.tei_parser import ParsedPaper, extract_body_text, extract_sections, parse_tei
from app.utils.normalization import normalize_doi, normalize_title

# fetch_tei takes the PDF path and returns raw TEI XML (the GROBID call, injected for testing).
TeiFetcher = Callable[[Path], str]


def _text_layer_quality(body_text: str, abstract: str | None, page_count: int | None) -> str:
    """Classify a PDF's text layer (SPEC §8.3): ``poor`` likely needs OCR, else ``good``."""
    chars = len(body_text or "") + len(abstract or "")
    pages = page_count or 1
    # Heuristic: a born-digital PDF yields hundreds+ of chars per page; near-empty ⇒ scanned.
    if chars < max(200, 100 * pages // 4):
        return "poor"
    return "good"


def _resolve_ocr_engine(
    ocr_backend: str, *, text_layer_quality: str | None, force_ocr: bool
) -> str | None:
    """Pick the OCR pre-step engine (``"ocrmypdf"`` / ``"pymupdf"``) or ``None``.

    Gated on the engine being installed AND either the admin selected an OCR backend and the text
    layer needs OCR, OR the caller forced it (#22). ``pymupdf`` is used only when that backend is
    selected; ``ocrmypdf`` covers the ``ocrmypdf`` backend and is the fallback engine for a forced
    re-OCR under the non-OCR backend (``none``), preserving the prior force behaviour.
    """
    needs = ocr_service.needs_ocr(text_layer_quality)
    if ocr_backend == "pymupdf":
        if (force_ocr or needs) and ocr_service.pymupdf_available():
            return "pymupdf"
        return None
    if (force_ocr or (ocr_backend == "ocrmypdf" and needs)) and ocr_service.ocrmypdf_available():
        return "ocrmypdf"
    return None


def store_parsed_extraction(
    db: Session,
    *,
    work: Work,
    parsed: ParsedPaper,
    source: str = "grobid",
    file: File | None = None,
    raw_tei_xml: str | None = None,
) -> dict[str, Any]:
    """Record assertions, promote safe canonical fields, and (re)create references."""
    promotable = not work.user_confirmed
    promoted: list[str] = []

    def assert_field(field_name: str, value: str | None, *, canonical: bool) -> None:
        if not value:
            return
        # Dedup (issue 1b): a re-extract that yields the same value for the same (work, field,
        # source) must not create an identical duplicate assertion. Reuse the existing row (keeping
        # its canonical flag in sync) and only insert when the value actually changed.
        existing = db.scalar(
            select(MetadataAssertion).where(
                MetadataAssertion.entity_type == "work",
                MetadataAssertion.entity_id == work.id,
                MetadataAssertion.field_name == field_name,
                MetadataAssertion.source == source,
                MetadataAssertion.value == value,
            )
        )
        if existing is not None:
            if canonical and not existing.selected_as_canonical:
                existing.selected_as_canonical = True
            existing.retrieved_at = datetime.now(UTC)
            return
        db.add(
            MetadataAssertion(
                entity_type="work",
                entity_id=work.id,
                field_name=field_name,
                value=value,
                source=source,
                selected_as_canonical=canonical,
            )
        )

    title_canonical = bool(
        promotable and parsed.title and work.canonical_metadata_source in (None, "filename")
    )
    if title_canonical:
        work.canonical_title = parsed.title
        work.normalized_title = normalize_title(parsed.title or "")
        work.canonical_metadata_source = source
        promoted.append("title")
    assert_field("title", parsed.title, canonical=title_canonical)

    abstract_canonical = bool(promotable and parsed.abstract and not work.abstract)
    if abstract_canonical:
        work.abstract = parsed.abstract
        promoted.append("abstract")
    assert_field("abstract", parsed.abstract, canonical=abstract_canonical)

    doi_canonical = bool(promotable and parsed.doi and not work.doi)
    if doi_canonical:
        work.doi = normalize_doi(parsed.doi)  # S3: never store a decorated DOI on the work
        promoted.append("doi")
    assert_field("doi", parsed.doi, canonical=doi_canonical)

    # Issue 11: promote the paper's own venue/year mined from the TEI header when GROBID supplies
    # them and the field is still empty (never overwriting a user-confirmed / already-set value).
    venue_canonical = bool(promotable and parsed.venue and not work.venue)
    if venue_canonical:
        work.venue = parsed.venue
        promoted.append("venue")
    assert_field("venue", parsed.venue, canonical=venue_canonical)

    year_canonical = bool(promotable and parsed.year and not work.year)
    if year_canonical:
        work.year = parsed.year
        promoted.append("year")
    assert_field("year", str(parsed.year) if parsed.year else None, canonical=year_canonical)

    if parsed.authors:
        assert_field("authors", "; ".join(parsed.authors), canonical=False)

    # Deterministic keyword extraction (SPEC §8.15.1) over the richest available text, and a
    # text-layer / needs-OCR signal for the file (SPEC §8.3).
    body_text = (extract_body_text(raw_tei_xml) if raw_tei_xml else "") or ""
    keyword_source = " ".join(part for part in (parsed.abstract, body_text) if part)
    if keyword_source:
        # Boost phrases that also appear in the title / abstract / section headings (issue 8).
        headings = (
            " ".join(label for label, _ in extract_sections(raw_tei_xml) if label)
            if raw_tei_xml
            else ""
        )
        boost = " ".join(part for part in (work.canonical_title, parsed.abstract, headings) if part)
        work.keywords = extract_keywords(keyword_source, top_k=12, boost_text=boost)
    if file is not None:
        file.text_layer_quality = _text_layer_quality(body_text, parsed.abstract, file.page_count)

    work.updated_at = datetime.now(UTC)

    source_tei: RawTeiDocument | None = None
    if file is not None and raw_tei_xml:
        db.execute(
            delete(RawTeiDocument).where(
                RawTeiDocument.file_id == file.id,
                RawTeiDocument.work_id == work.id,
                RawTeiDocument.source == source,
            )
        )
        source_tei = RawTeiDocument(
            file_id=file.id,
            work_id=work.id,
            source=source,
            tei_xml=raw_tei_xml,
        )
        db.add(source_tei)
        db.flush()

    # References and mentions are owned by this work's extraction: replace idempotently. References
    # are now shared canonical rows (batch 12), so we unlink *this work's* citation edges + mentions
    # and find-or-create the canonical references, rather than deleting reference rows outright.
    db.execute(delete(CitationMention).where(CitationMention.citing_work_id == work.id))
    prior_ref_ids = set(
        db.scalars(
            select(ReferenceCitation.reference_id).where(
                ReferenceCitation.citing_work_id == work.id
            )
        ).all()
    )
    db.execute(delete(ReferenceCitation).where(ReferenceCitation.citing_work_id == work.id))
    reference_by_key: dict[str, Reference] = {}
    # Two distinct bib entries can dedup to the *same* canonical reference (shared rows, batch 12);
    # a work cites each canonical reference at most once, so guard the edge against duplicates to
    # avoid violating uq_reference_citation.
    cited_reference_ids: set[Any] = set()
    for index, reference in enumerate(parsed.references):
        saved = find_or_create_reference(
            db,
            title=reference.title,
            doi=reference.doi,
            arxiv_id=reference.arxiv_id,
            year=reference.year,
            raw_citation=reference.raw_citation,
            authors=list(reference.authors) if reference.authors else None,
        )
        if saved.id not in cited_reference_ids:
            cited_reference_ids.add(saved.id)
            db.add(
                ReferenceCitation(
                    reference_id=saved.id,
                    citing_work_id=work.id,
                    source_tei_id=source_tei.id if source_tei else None,
                )
            )
        if reference.key:
            reference_by_key[reference.key] = saved
        reference_by_key[f"b{index}"] = saved
    db.flush()

    # Prune canonical references this work no longer cites and nobody else does either (orphans).
    kept_ids = {ref.id for ref in reference_by_key.values()}
    stale_ids = prior_ref_ids - kept_ids
    if stale_ids:
        still_linked = set(
            db.scalars(
                select(ReferenceCitation.reference_id).where(
                    ReferenceCitation.reference_id.in_(stale_ids)
                )
            ).all()
        )
        orphaned = stale_ids - still_linked
        if orphaned:
            db.execute(delete(Reference).where(Reference.id.in_(orphaned)))

    # Reference→library matching (batch 12): resolve each canonical reference this extraction touched
    # against the local library, before building mentions (so a mention inherits the resolution). The
    # fuzzy-as-confirmed runtime toggle is read from the AppConfig singleton (Phase 3).
    fuzzy_as_confirmed = effective_use_fuzzy_match_as_confirmed(db)
    run_matching_for_references(
        db,
        set(reference_by_key.values()),
        settings=get_settings(),
        fuzzy_as_confirmed=fuzzy_as_confirmed,
    )

    # Reverse direction: this work's title/DOI may have just been promoted above, making it the
    # missing target of references and cached citing papers elsewhere in the library. Previously
    # only the manual create/import endpoints reverse-rescanned, so papers arriving through the
    # main upload→GROBID path were never linked as targets until a full library rescan.
    from app.services.citing_papers import (
        rescan_external_papers_for_new_work,  # noqa: PLC0415 - cycle guard
    )

    rescan_references_for_new_work(db, work, fuzzy_as_confirmed=fuzzy_as_confirmed)
    rescan_external_papers_for_new_work(db, work)

    mention_count = 0
    for mention in parsed.citation_mentions:
        reference = reference_by_key.get(mention.reference_key)
        if reference is None:
            continue
        db.add(
            CitationMention(
                citing_work_id=work.id,
                reference_id=reference.id,
                resolved_cited_work_id=reference.resolved_work_id,
                marker_text=mention.marker_text,
                section_label=mention.section_label,
                context_before=mention.context_before,
                context_sentence=mention.context_sentence,
                context_after=mention.context_after,
                page=mention.page,
                pdf_coordinates=mention.pdf_coordinates or None,
                source_tei_id=source_tei.id if source_tei else None,
            )
        )
        mention_count += 1

    return {
        "promoted": promoted,
        "reference_count": len(parsed.references),
        "citation_mention_count": mention_count,
        "author_count": len(parsed.authors),
        "raw_tei_stored": source_tei is not None,
    }


def extract_and_store(
    db: Session,
    *,
    file: File,
    fetch_tei: TeiFetcher,
    actor_user_id=None,
    settings: Settings | None = None,
    force_ocr: bool = False,
) -> dict[str, Any]:
    """Run extraction for a file: locate its work + path, fetch/parse TEI, persist.

    The PDF path is resolved through the shared resolver, so both server-folder
    (``server_path``) and uploaded managed-library (``managed_path``) files are extractable
    and validated against their configured roots (AUDIT A1).
    """
    settings = settings or get_settings()
    link = db.scalar(select(FileWorkLink).where(FileWorkLink.file_id == file.id))
    if link is None:
        raise ValueError("File has no linked work to attach extraction to")
    work = db.get(Work, link.work_id)
    if work is None:
        raise ValueError("Linked work not found")

    pdf_path = resolve_backend_readable_pdf_path(db, file=file, settings=settings)

    # Effective OCR/advanced-extraction backend + languages: DB row (runtime toggle) overlaid on
    # Settings, honouring the admin's choice like the topic job does. ocr_language is tesseract
    # syntax and may be multi like "eng+spa".
    ai_config = get_ai_config(db, settings=settings)
    ocr_backend = ai_config.ocr_backend
    ocr_language = ai_config.ocr_language

    # OCR pre-step: when an OCR backend is selected and the text layer is poor/none/unknown, add a
    # searchable text layer to a *transient* copy and feed that to GROBID. OCR never fails the
    # extraction (the OCR helpers swallow errors and return the original path on failure/skip).
    ocr_result: ocr_service.OcrResult | None = None
    tei_source_path = pdf_path
    # Pick the OCR engine to run (or None): ocrmypdf or pymupdf, gated on availability + the
    # text-layer-quality/#22-force rule. force_ocr uses the selected OCR engine (pymupdf when that
    # backend is chosen, else ocrmypdf) regardless of the current quality.
    ocr_engine = _resolve_ocr_engine(
        ocr_backend, text_layer_quality=file.text_layer_quality, force_ocr=force_ocr
    )
    if ocr_engine is not None:
        with tempfile.TemporaryDirectory(prefix="paracord-ocr-") as scratch:
            if ocr_engine == "pymupdf":
                ocr_result = ocr_service.pymupdf_ocr(
                    pdf_path,
                    out_dir=Path(scratch),
                    language=ocr_language,
                    timeout=settings.ocr_timeout_seconds,
                )
            else:
                ocr_result = ocr_service.maybe_ocr(
                    pdf_path,
                    text_layer_quality=file.text_layer_quality,
                    out_dir=Path(scratch),
                    timeout=settings.ocr_timeout_seconds,
                    language=ocr_language,
                    skip_if_good=settings.ocr_skip_if_text_layer_good and not force_ocr,
                )
            tei_source_path = ocr_result.output_pdf_path
            tei_xml = fetch_tei(tei_source_path)
            # Persist the searchable OCR'd copy to a DERIVED location so the reader can serve
            # selectable text (never mutate the content-addressed original). Best-effort; the temp
            # copy is about to be deleted, so it must be persisted inside this block.
            if ocr_result.ran and tei_source_path != pdf_path:
                save_derived_ocr_pdf(settings, file.sha256, tei_source_path)
    else:
        tei_xml = fetch_tei(pdf_path)

    parsed = parse_tei(tei_xml)
    summary = store_parsed_extraction(db, work=work, parsed=parsed, file=file, raw_tei_xml=tei_xml)

    # OCR provenance in the summary + audit. When OCR actually ran, "ocr_added" wins over the
    # post-GROBID text-layer recompute (the file now carries a text layer we added).
    ocr_ran = bool(ocr_result and ocr_result.ran)
    if ocr_ran and ocr_result is not None:
        file.text_layer_quality = ocr_result.text_layer_quality or "ocr_added"
    summary["ocr_backend"] = ocr_backend
    summary["ocr_ran"] = ocr_ran
    summary["ocr_engine"] = ocr_result.engine if ocr_result else None
    summary["ocr_error"] = ocr_result.error if ocr_result else None
    # Surface availability + text-layer quality so the UI can explain a textless scan (#22):
    # "OCR not installed" vs "OCR ran" vs "text layer already good".
    summary["ocr_available"] = (
        ocr_service.pymupdf_available()
        if ocr_backend == "pymupdf"
        else ocr_service.ocrmypdf_available()
    )
    summary["ocr_forced"] = force_ocr
    summary["text_layer_quality"] = file.text_layer_quality

    record_event(
        db,
        "extraction.completed",
        actor_user_id=actor_user_id,
        entity_type="work",
        entity_id=str(work.id),
        details={"file_id": str(file.id), "source": "grobid", **summary},
    )
    return summary
