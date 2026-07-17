"""Multi-PDF staging import (batch 10, issue 1).

The flow "extract before storing records":

1. :func:`stage_pdfs` stores each uploaded PDF content-addressed (dedup-safe) and creates a
   staging item — but no ``Work``/``FileWorkLink`` yet.
2. :func:`extract_staging_item` runs GROBID on a staged PDF and records the parsed metadata + raw
   TEI + detected collisions on the item (still no Work). Runs in a worker, or inline as a fallback.
3. :func:`commit_staging` mints the real ``Work`` + ``FileWorkLink`` for accepted items, applying
   the *stored* TEI (no GROBID re-run); skipped items are dropped. "Import directly" mode uses
   :func:`auto_decisions` to accept every extracted, non-blocked item automatically.

Nothing here fails the whole batch on a single bad PDF — a failure is recorded on its item.
"""

import uuid
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.file import File, FileWorkLink
from app.models.import_staging import ImportStagingBatch, ImportStagingItem
from app.models.user import User
from app.models.work import Work
from app.services import access
from app.services.audit import record_event
from app.services.extraction import store_parsed_extraction
from app.services.file_paths import resolve_backend_readable_pdf_path
from app.services.identifiers import arxiv_base_id
from app.services.shelf_membership import add_work_to_shelf_checked
from app.services.storage import mark_extraction_requested, probe_pdf_openable, stage_managed_file
from app.services.tei_parser import ParsedPaper, parse_tei
from app.utils.normalization import normalize_doi, normalize_title
from app.workers.queue import enqueue_enrichment, enqueue_extraction

# fetch_tei takes a PDF path and returns raw TEI XML (the GROBID call, injected for testing).
TeiFetcher = Callable[[Path], str]

MAX_STAGING_FILES = 100
_MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # 200 MB, matching the single-upload cap

# Terminal item states — extraction is done (either way).
TERMINAL_STATES = {"extracted", "extract_failed", "committed", "skipped"}
# Collision signals that block auto-create in "import directly" mode (owner decision: PDF + DOI).
BLOCKING_SIGNALS = ("same_pdf", "same_doi")


def _validate_pdf(pdf_bytes: bytes) -> str | None:
    """Return an error message if the bytes are not an acceptable PDF, else None."""
    if len(pdf_bytes) > _MAX_UPLOAD_BYTES:
        return "Exceeds 200 MB limit"
    if len(pdf_bytes) < 4 or pdf_bytes[:4] != b"%PDF":
        return "Not a valid PDF"
    return probe_pdf_openable(pdf_bytes)  # encrypted / unopenable → message, else None


def stage_pdfs(
    db: Session,
    *,
    actor: User,
    uploads: Sequence[tuple[str, bytes]],
    mode: str = "preview",
    target_shelf_id=None,
    settings: Settings | None = None,
) -> ImportStagingBatch:
    """Persist a batch of uploaded PDFs to staging (content-addressed), one item per file.

    A file that fails validation becomes an ``extract_failed`` item with an error — it never aborts
    the batch. Valid files are stored and left ``pending`` (extraction is scheduled by the caller).
    """
    settings = settings or get_settings()
    batch = ImportStagingBatch(
        created_by_user_id=actor.id,
        mode="direct" if mode == "direct" else "preview",
        status="extracting",
        target_shelf_id=target_shelf_id,
    )
    db.add(batch)
    db.flush()
    for filename, pdf_bytes in uploads:
        safe_name = Path(filename or "upload.pdf").name or "upload.pdf"
        error = _validate_pdf(pdf_bytes)
        if error is not None:
            db.add(
                ImportStagingItem(
                    batch_id=batch.id, filename=safe_name, status="extract_failed", error=error
                )
            )
            continue
        file, _created = stage_managed_file(
            db, filename=safe_name, pdf_bytes=pdf_bytes, settings=settings
        )
        db.add(
            ImportStagingItem(
                batch_id=batch.id,
                file_id=file.id,
                filename=safe_name,
                sha256=file.sha256,
                status="pending",
            )
        )
    db.flush()
    record_event(
        db,
        "import.staging.created",
        actor_user_id=actor.id,
        entity_type="import_staging_batch",
        entity_id=str(batch.id),
        details={"mode": batch.mode, "file_count": len(uploads)},
    )
    return batch


def _live_work_refs(db: Session, work_ids: Iterable) -> list[dict]:
    """Return [{work_id, title}] for the given ids that are live (non-shadow) works."""
    ids = list({wid for wid in work_ids if wid is not None})
    if not ids:
        return []
    rows = db.execute(
        select(Work.id, Work.canonical_title).where(Work.id.in_(ids), Work.merged_into_id.is_(None))
    ).all()
    return [{"work_id": str(wid), "title": title} for wid, title in rows]


def detect_collisions(
    db: Session, *, sha256: str | None, doi: str | None, title: str | None
) -> dict[str, list[dict]]:
    """Find existing papers that collide with a staged PDF: same file, DOI, or normalized title."""
    result: dict[str, list[dict]] = {}

    if sha256:
        # Works already linked to a File with this content hash (the meaningful "same PDF" case).
        linked_ids = db.scalars(
            select(FileWorkLink.work_id)
            .join(File, File.id == FileWorkLink.file_id)
            .where(File.sha256 == sha256)
        ).all()
        same_pdf = _live_work_refs(db, linked_ids)
        if same_pdf:
            result["same_pdf"] = same_pdf

    if doi:
        norm = normalize_doi(doi)
        if norm:
            doi_ids = db.scalars(select(Work.id).where(Work.doi == norm)).all()
            same_doi = _live_work_refs(db, doi_ids)
            if same_doi:
                result["same_doi"] = same_doi

    if title:
        norm_title = normalize_title(title)
        if norm_title:
            title_ids = db.scalars(select(Work.id).where(Work.normalized_title == norm_title)).all()
            same_title = _live_work_refs(db, title_ids)
            if same_title:
                result["same_title"] = same_title
    return result


def extract_staging_item(
    db: Session,
    *,
    item: ImportStagingItem,
    fetch_tei: TeiFetcher,
    settings: Settings | None = None,
) -> None:
    """Run a record-free GROBID extraction for one staged item and record the outcome.

    On success stores the parsed preview metadata + raw TEI + detected collisions and marks the
    item ``extracted``. On any failure marks it ``extract_failed`` with a message (still recording a
    same-PDF collision, which needs no metadata). Never raises — a bad PDF is contained to its item.
    """
    settings = settings or get_settings()
    item.status = "extracting"
    item.updated_at = datetime.now(UTC)
    file = db.get(File, item.file_id) if item.file_id else None
    if file is None:
        item.status = "extract_failed"
        item.error = "Staged file is missing"
        return
    try:
        pdf_path = resolve_backend_readable_pdf_path(db, file=file, settings=settings)
        tei_xml = fetch_tei(pdf_path)
        parsed = parse_tei(tei_xml)
        item.tei_xml = tei_xml
        item.parsed = {
            "title": parsed.title,
            "authors": parsed.authors,
            "year": parsed.year,
            "doi": parsed.doi,
            "venue": parsed.venue,
            "abstract": parsed.abstract,
        }
        item.duplicates = detect_collisions(
            db, sha256=item.sha256, doi=parsed.doi, title=parsed.title
        )
        item.status = "extracted"
    except Exception as exc:  # noqa: BLE001 - contain a bad PDF / GROBID error to this item
        item.status = "extract_failed"
        item.error = str(exc) or exc.__class__.__name__
        # Same-PDF collision needs no metadata, so still surface it on failure.
        item.duplicates = detect_collisions(db, sha256=item.sha256, doi=None, title=None)
    item.updated_at = datetime.now(UTC)


def requeue_stalled_items(db: Session, batch: ImportStagingBatch) -> int:
    """Re-enqueue staged items whose extraction job died (S-batch item 2 "stuck pending").

    A worker restart / crash mid-extraction leaves items parked in ``pending``/``extracting`` with
    no live RQ job — previously forever (the batch never reached ``ready`` and the UI showed no
    actions). Called from the polling GET: for each such item, re-enqueue (the deterministic job id
    makes this a no-op while a live job exists); if the queue is unavailable, extract ONE item
    inline per poll so the batch still drains without Redis. Returns how many were kicked.
    """
    from app.workers.queue import enqueue_staging_extraction

    stalled = [i for i in _batch_items(db, batch) if i.status in ("pending", "extracting")]
    if not stalled:
        return 0
    kicked = 0
    inline_done = False
    for item in stalled:
        if enqueue_staging_extraction(item.id) is not None:
            kicked += 1
        elif not inline_done:
            # Queue down: drain one item per poll inline (bounded request time).
            from app.services.grobid_client import GrobidClient

            settings = get_settings()
            client = GrobidClient(settings.grobid_url, settings=settings)
            extract_staging_item(
                db, item=item, fetch_tei=client.process_fulltext_document_sync, settings=settings
            )
            inline_done = True
            kicked += 1
    return kicked


def _batch_items(db: Session, batch: ImportStagingBatch) -> list[ImportStagingItem]:
    return list(
        db.scalars(select(ImportStagingItem).where(ImportStagingItem.batch_id == batch.id)).all()
    )


def finalize_if_ready(db: Session, batch: ImportStagingBatch) -> bool:
    """Flip a batch from ``extracting`` to ``ready`` once every item is terminal. Returns True then."""
    if batch.status != "extracting":
        return False
    items = db.scalars(
        select(ImportStagingItem).where(ImportStagingItem.batch_id == batch.id)
    ).all()
    if items and all(i.status in TERMINAL_STATES for i in items):
        batch.status = "ready"
        batch.updated_at = datetime.now(UTC)
        return True
    return False


def _item_blocked(item: ImportStagingItem) -> str | None:
    """Return the first blocking collision signal present on the item, else None."""
    dups = item.duplicates or {}
    for signal in BLOCKING_SIGNALS:
        if dups.get(signal):
            return signal
    return None


def auto_decisions(items: Sequence[ImportStagingItem]) -> list[dict[str, Any]]:
    """Compute "import directly" decisions: accept extracted, non-blocked items; skip the rest.

    Returns ``[{item_id, action, reason?}]``. Skips carry a human ``reason`` (extraction failed, or
    the blocking collision) so the caller can report exactly why each paper was not created.
    """
    decisions: list[dict[str, Any]] = []
    for item in items:
        if item.status != "extracted":
            decisions.append(
                {"item_id": str(item.id), "action": "skip", "reason": item.error or "not extracted"}
            )
            continue
        blocked = _item_blocked(item)
        if blocked:
            decisions.append(
                {"item_id": str(item.id), "action": "skip", "reason": f"duplicate ({blocked})"}
            )
            continue
        decisions.append({"item_id": str(item.id), "action": "accept"})
    return decisions


def _title_for_item(item: ImportStagingItem) -> str:
    """Best title for a new Work: parsed title, else the filename stem."""
    parsed = item.parsed or {}
    title = (parsed.get("title") or "").strip()
    if title:
        return title
    return Path(item.filename).stem.strip() or "Untitled paper"


def _item_parsed_paper(item: ImportStagingItem) -> ParsedPaper | None:
    """Re-parse the item's stored TEI, honouring a preview-time DOI override.

    The user can edit/clear a staged item's DOI in the preview (the book-vs-chapter same-DOI
    case); ``item.parsed["doi"]`` is the edited truth while ``tei_xml`` still carries GROBID's
    original, so the parsed paper is patched to match before anything is stored.
    """
    if not item.tei_xml:
        return None
    parsed = parse_tei(item.tei_xml)
    parsed.doi = normalize_doi((item.parsed or {}).get("doi") or "") or None
    return parsed


def set_item_doi(db: Session, *, item: ImportStagingItem, doi: str | None) -> None:
    """Preview-time DOI override for a staged item (edit or clear), with collisions re-detected."""
    normalized = normalize_doi(doi or "") or None
    parsed = dict(item.parsed or {})
    parsed["doi"] = normalized
    item.parsed = parsed
    item.duplicates = detect_collisions(
        db, sha256=item.sha256, doi=normalized, title=parsed.get("title")
    )
    item.updated_at = datetime.now(UTC)


def append_item_to_work(
    db: Session, *, item: ImportStagingItem, work: Work, actor: User
) -> tuple[bool, str | None]:
    """Attach a staged PDF to an EXISTING paper (the collision "append PDF" action).

    Links the staged file to ``work`` (content-addressed → a re-attach is a no-op) and applies
    the stored preview extraction ONLY when the paper has no extraction yet — a paper that was
    already extracted from another PDF keeps its references/sections untouched (the second PDF
    is just an alternate file). Returns ``(applied_extraction, error)``.
    """
    from app.models.citation import RawTeiDocument

    if item.file_id is None:
        return False, "staged file is missing"
    existing_link = db.scalar(
        select(FileWorkLink).where(
            FileWorkLink.file_id == item.file_id, FileWorkLink.work_id == work.id
        )
    )
    if existing_link is None:
        db.add(FileWorkLink(file_id=item.file_id, work_id=work.id, user_confirmed=True))
    file = db.get(File, item.file_id)

    has_extraction = (
        db.scalar(select(RawTeiDocument.id).where(RawTeiDocument.work_id == work.id)) is not None
    )
    parsed = _item_parsed_paper(item)
    if has_extraction or parsed is None or file is None:
        # Keep the paper's existing extraction (or there is nothing to apply): attach only. When
        # the paper has no extraction AND this item has no TEI, owe a fresh extraction (D7).
        if not has_extraction and file is not None:
            mark_extraction_requested(file)
        return False, None
    store_parsed_extraction(db, work=work, parsed=parsed, file=file, raw_tei_xml=item.tei_xml)
    file.status = "extracted"
    # store_parsed_extraction only promotes into an empty, unclaimed DOI; nothing else to sync.
    work.arxiv_base_id = arxiv_base_id(work.arxiv_id)
    return True, None


def _create_work_from_item(
    db: Session, *, item: ImportStagingItem, actor: User, settings: Settings
) -> tuple[Work, bool]:
    """Mint a Work + FileWorkLink for an accepted item and apply its stored extraction.

    Returns ``(work, needs_extraction)`` — ``needs_extraction`` is True when the preview extraction
    had failed, so the caller queues a fresh extraction post-commit (which chains enrichment itself).
    """
    parsed_doi = normalize_doi((item.parsed or {}).get("doi") or "") or None
    title = _title_for_item(item)
    work = Work(
        canonical_title=title,
        normalized_title=normalize_title(title),
        canonical_metadata_source="grobid" if item.tei_xml else "filename",
    )
    db.add(work)
    db.flush()
    if item.file_id is not None:
        db.add(FileWorkLink(file_id=item.file_id, work_id=work.id, user_confirmed=False))
    file = db.get(File, item.file_id) if item.file_id else None

    needs_extraction = False
    parsed = _item_parsed_paper(item)  # honours a preview-time DOI edit/clear
    if parsed is not None and file is not None:
        # Apply the extraction captured at preview time — no GROBID re-run.
        store_parsed_extraction(db, work=work, parsed=parsed, file=file, raw_tei_xml=item.tei_xml)
        file.status = "extracted"
    elif file is not None:
        # Extraction failed at preview — create the paper anyway and owe a fresh extraction (D7).
        mark_extraction_requested(file)
        needs_extraction = True
    # store_parsed_extraction only promotes into an empty DOI; keep the accepted DOI in sync.
    if parsed_doi and not work.doi:
        work.doi = normalize_doi(parsed_doi)
    work.arxiv_base_id = arxiv_base_id(work.arxiv_id)
    return work, needs_extraction


def commit_staging(
    db: Session,
    *,
    actor: User,
    batch: ImportStagingBatch,
    decisions: Sequence[dict[str, Any]],
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Create Works for accepted items, skip the rest; finalize the batch. Returns a summary.

    Each decision is ``{item_id, action: accept|skip}``. Only ``pending``/``extracted``/
    ``extract_failed`` items are actionable (already-committed items are left alone). Accepted items
    are added to the batch's target shelf when one was set.

    Sequential/partial commits (S-batch item 2): a commit that leaves undecided or still-extracting
    items keeps the batch OPEN — the user can import already-extracted items while the rest of the
    batch is still processing, repeatedly. The batch flips to ``committed`` only once no item
    remains in ``pending``/``extracting``/``extracted``.
    """
    settings = settings or get_settings()
    by_id = {
        str(i.id): i
        for i in db.scalars(
            select(ImportStagingItem).where(ImportStagingItem.batch_id == batch.id)
        ).all()
    }
    created: list[str] = []
    appended: list[str] = []
    enrich_ids: list[str] = []  # TEI already applied → just enrich
    extract_file_ids: list[str] = []  # preview extraction failed → (re)extract, which chains enrich
    skipped = 0
    warnings: list[str] = []
    # DOIs minted earlier in THIS commit, so a same-DOI sibling (book vs. chapter) gets a precise
    # message naming the first file instead of a savepoint unique-violation autopsy.
    doi_minted_by: dict[str, str] = {}

    def _doi_owner_hint(doi: str | None) -> str | None:
        """A precise, actionable reason when ``doi`` is already taken, else None."""
        if not doi:
            return None
        sibling = doi_minted_by.get(doi)
        if sibling:
            return (
                f"same DOI as “{sibling}” in this batch — edit/clear one DOI in the preview, "
                "or import only one"
            )
        owner = db.scalar(select(Work).where(Work.doi == doi, Work.merged_into_id.is_(None)))
        if owner is not None:
            return (
                f"same DOI as “{owner.canonical_title or owner.id}” — choose “Attach PDF to it” "
                "in the preview instead of creating a new paper"
            )
        return None

    for decision in decisions:
        item = by_id.get(str(decision.get("item_id")))
        if item is None or item.status in ("committed", "skipped"):
            continue
        action = decision.get("action")

        if action == "append":
            # Attach this staged PDF to an existing paper (collision resolution). ACL-checked
            # like any other modification of that paper.
            try:
                target_id = uuid.UUID(str(decision.get("target_work_id")))
            except (ValueError, TypeError):
                warnings.append(f"{item.filename}: no valid paper chosen to attach to")
                continue
            target = db.get(Work, target_id)
            if target is None or target.merged_into_id is not None:
                warnings.append(f"{item.filename}: the chosen paper no longer exists")
                continue
            if not access.can_modify_work(db, actor, target):
                warnings.append(
                    f"{item.filename}: you do not have permission to modify "
                    f"“{target.canonical_title or target.id}”"
                )
                continue
            try:
                with db.begin_nested():
                    applied, error = append_item_to_work(db, item=item, work=target, actor=actor)
                    if error:
                        raise ValueError(error)
                    db.flush()
            except Exception as exc:  # noqa: BLE001 - one bad item must not abort the commit
                item.status = "extract_failed"
                item.error = f"append failed: {exc}".strip()[:500]
                warnings.append(f"{item.filename}: could not attach ({exc})")
                item.updated_at = datetime.now(UTC)
                continue
            item.created_work_id = target.id
            item.status = "committed"
            item.updated_at = datetime.now(UTC)
            appended.append(str(target.id))
            if applied:
                enrich_ids.append(str(target.id))
            elif item.file_id is not None and item.tei_xml is None:
                extract_file_ids.append(str(item.file_id))
            continue

        if action != "accept":
            item.status = "skipped"
            item.updated_at = datetime.now(UTC)
            skipped += 1
            continue

        item_doi = normalize_doi((item.parsed or {}).get("doi") or "") or None
        hint = _doi_owner_hint(item_doi)
        if hint:
            item.status = "extract_failed"
            item.error = f"commit refused: {hint}"[:500]
            warnings.append(f"{item.filename}: {hint}")
            item.updated_at = datetime.now(UTC)
            continue
        # Per-item SAVEPOINT so one bad item (e.g. a unique conflict this pre-check didn't cover)
        # rolls back only itself and the rest of the commit still proceeds.
        try:
            with db.begin_nested():
                work, needs_extraction = _create_work_from_item(
                    db, item=item, actor=actor, settings=settings
                )
                db.flush()  # surface a DOI/unique conflict inside the savepoint
                if batch.target_shelf_id is not None:
                    add_work_to_shelf_checked(
                        db, shelf_id=batch.target_shelf_id, work_id=work.id, actor=actor
                    )
        except Exception as exc:  # noqa: BLE001 - one bad item must not abort the whole commit
            item.status = "extract_failed"
            item.error = f"commit failed: {exc}".strip()[:500]
            warnings.append(f"{item.filename}: could not create paper (possible duplicate DOI)")
            item.updated_at = datetime.now(UTC)
            continue
        if item_doi:
            doi_minted_by[item_doi] = item.filename
        item.created_work_id = work.id
        item.status = "committed"
        item.updated_at = datetime.now(UTC)
        created.append(str(work.id))
        if needs_extraction and item.file_id is not None:
            extract_file_ids.append(str(item.file_id))
        else:
            enrich_ids.append(str(work.id))

    remaining = [i for i in by_id.values() if i.status in ("pending", "extracting", "extracted")]
    if not remaining:
        batch.status = "committed"
    batch.updated_at = datetime.now(UTC)
    record_event(
        db,
        "import.staging.committed",
        actor_user_id=actor.id,
        entity_type="import_staging_batch",
        entity_id=str(batch.id),
        details={
            "created": len(created),
            "appended": len(appended),
            "skipped": skipped,
            "warnings": warnings,
        },
    )
    return {
        "batch_id": str(batch.id),
        "created_work_ids": created,
        "created": len(created),
        "appended_work_ids": appended,
        "appended": len(appended),
        "skipped": skipped,
        "warnings": warnings,
        "_enrich_work_ids": enrich_ids,
        "_extract_file_ids": extract_file_ids,
    }


def enqueue_post_commit_jobs(summary: dict[str, Any]) -> None:
    """Best-effort background jobs after a committed staging batch (queue may be unavailable).

    Enrich TEI-applied works directly; (re)extract works whose preview extraction had failed — the
    extraction job chains enrichment on its own, so those are not enriched here.
    """
    for work_id in summary.get("_enrich_work_ids", []):
        enqueue_enrichment(work_id)
    for file_id in summary.get("_extract_file_ids", []):
        enqueue_extraction(file_id)
