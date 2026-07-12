"""Managed library storage and server-folder import service."""

import hashlib
import logging
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.file import File, FileWorkLink, Location
from app.models.metadata import MetadataAssertion
from app.models.source import ImportBatch, Source
from app.models.user import User
from app.models.work import Work
from app.services.audit import record_event
from app.services.identifiers import arxiv_base_id as _arxiv_base_id
from app.utils.normalization import normalize_title

logger = logging.getLogger(__name__)

# New-style arXiv id, optionally with a version suffix (e.g. 2106.01345 / 1706.03762v5).
_ARXIV_ID_RE = re.compile(r"^(\d{4}\.\d{4,5})(v\d+)?$")


def mark_extraction_requested(file: File) -> None:
    """Flag ``file`` as owed a GROBID extraction, in the caller's current transaction (D7).

    Set in the SAME commit that intends to extract the file so the durable marker and the file row
    land together; the extraction worker clears it on terminal success/failure and the startup
    recovery sweep re-enqueues anything still marked (e.g. an enqueue lost to a dead Redis).

    Also resets the F2 attempt counter, so a user-initiated (re-)extract always gets a fresh retry
    budget even if a prior run gave up as terminal. Automatic retries (RQ) and the recovery sweep go
    straight through ``enqueue_extraction`` and do NOT pass here, so they stay bounded by the cap.
    """
    file.extraction_requested_at = datetime.now(UTC)
    file.extraction_attempts = 0


def file_ids_pending_extraction(db: Session, source_id) -> list:
    """Return file IDs for a source whose linked work has no GROBID extraction yet."""
    extracted_works = select(MetadataAssertion.entity_id).where(
        MetadataAssertion.entity_type == "work",
        MetadataAssertion.source == "grobid",
    )
    stmt = (
        select(File.id)
        .join(Location, Location.file_id == File.id)
        .join(FileWorkLink, FileWorkLink.file_id == File.id)
        .where(Location.source_id == source_id, FileWorkLink.work_id.notin_(extracted_works))
        .distinct()
    )
    return list(db.scalars(stmt).all())


def content_addressed_path(root: Path, sha256: str) -> Path:
    """Return managed-library path for a SHA-256 digest."""
    if len(sha256) != 64:
        raise ValueError("Expected a 64-character SHA-256 digest")
    return root / sha256[:2] / sha256[2:4] / f"{sha256}.pdf"


def configured_server_roots(settings: Settings) -> dict[str, Path]:
    """Return the read-only ``server.yaml`` server-folder roots keyed by stable alias."""
    roots: dict[str, Path] = {}
    for index, entry in enumerate(settings.server_allowed_roots):
        alias: str | None = None
        raw_path: str | None = None
        if isinstance(entry, str):
            raw_path = entry
        elif isinstance(entry, dict):
            alias = str(entry.get("alias") or entry.get("name") or "").strip() or None
            raw_path = entry.get("path")
        if not raw_path:
            continue
        path = Path(str(raw_path)).expanduser().resolve()
        roots[alias or path.name or f"root-{index + 1}"] = path
    return roots


def db_server_roots(db: Session) -> dict[str, Path]:
    """Return the GUI-managed (DB-backed) server-folder roots keyed by alias.

    Tolerant of narrow unit-test schemas that don't create the ``import_roots`` table: absent table
    -> no DB roots (so the merged set is just the yaml entries).
    """
    from sqlalchemy import inspect

    from app.models.import_root import ImportRoot

    if not inspect(db.get_bind()).has_table(ImportRoot.__tablename__):
        return {}
    roots: dict[str, Path] = {}
    for row in db.scalars(select(ImportRoot)).all():
        roots[row.alias] = Path(str(row.path)).expanduser().resolve()
    return roots


def merged_server_roots(db: Session, settings: Settings) -> dict[str, Path]:
    """Return the MERGED allowed server-folder roots: read-only yaml entries + DB entries.

    The yaml entries take precedence on an alias clash (they are immutable and cannot be weakened by
    a DB row). Used by BOTH the import validation and the listing API so they never diverge.
    """
    merged = dict(db_server_roots(db))
    merged.update(configured_server_roots(settings))  # yaml wins on clash
    return merged


def create_server_folder_source(
    db: Session,
    *,
    settings: Settings,
    name: str,
    path_alias: str,
    actor: User,
) -> Source:
    """Create a server-folder source from a configured (yaml or GUI-managed) root alias."""
    roots = merged_server_roots(db, settings)
    root = roots.get(path_alias)
    if root is None:
        raise ValueError("Unknown server-folder alias")
    if not root.exists() or not root.is_dir():
        raise ValueError("Configured server-folder root is not a directory")

    canonical_root_hash = hashlib.sha256(str(root).encode("utf-8")).hexdigest()
    source = Source(
        type="server_folder",
        name=name,
        owner_user_id=actor.id,
        path_alias=path_alias,
        canonical_root_hash=canonical_root_hash,
        config={"root_path": str(root)},
    )
    db.add(source)
    db.flush()
    record_event(
        db,
        "source.folder_added",
        actor_user_id=actor.id,
        entity_type="source",
        entity_id=str(source.id),
        details={"name": name, "path_alias": path_alias},
    )
    return source


def import_server_folder(
    db: Session,
    *,
    source: Source,
    actor: User,
    recursive: bool = True,
) -> ImportBatch:
    """Scan a configured server-folder source and import PDF file identities.

    Transaction shape (D9): the batch row is committed up front in its own short transaction so it
    survives even if the scan later dies, each file is imported inside its own SAVEPOINT so one bad
    file rolls back only that file (not the whole batch), and the status/stats are finalized in a
    single commit at the end. Partial imports are therefore visible: a scan that hits some
    unreadable files still commits the files it could import (with an ``errors`` count in the stats)
    instead of rolling the whole batch back on the first failure.
    """
    if source.type != "server_folder" or not source.is_active:
        raise ValueError("Source is not an active server folder")

    root = _source_root(source)
    started_at = datetime.now(UTC)
    batch = ImportBatch(
        created_by_user_id=actor.id,
        source_id=source.id,
        input_type="folder",
        status="running",
        settings={"recursive": recursive},
        started_at=started_at,
        stats={"seen": 0, "created_files": 0, "created_works": 0, "existing_files": 0, "errors": 0},
    )
    db.add(batch)
    db.commit()  # D9: persist the batch row up front so it (and its final status) survives a crash
    db.refresh(batch)
    batch_id = batch.id

    stats = dict(batch.stats or {})
    try:
        pattern = "**/*.pdf" if recursive else "*.pdf"
        for pdf_path in sorted(root.glob(pattern)):
            if not pdf_path.is_file():
                continue
            stats["seen"] += 1
            try:
                _assert_inside_root(root, pdf_path)
                with db.begin_nested():  # D9: per-file savepoint — a bad file never aborts the scan
                    result = _import_pdf_path(
                        db, source=source, pdf_path=pdf_path, import_batch_id=batch_id
                    )
                stats["created_files"] += int(result["created_file"])
                stats["created_works"] += int(result["created_work"])
                stats["existing_files"] += int(not result["created_file"])
            except Exception:  # noqa: BLE001 - isolate + record one bad file, keep importing
                stats["errors"] += 1
                logger.warning("Folder import: skipping file %s", pdf_path, exc_info=True)
        # Mark every newly imported file owed an extraction in the SAME commit that persists them
        # (D7 invariant): the durable marker and the file rows land together. Tolerate the narrow
        # unit-test schema that omits the metadata_assertions table the pending query joins on.
        from sqlalchemy import inspect

        if inspect(db.get_bind()).has_table(MetadataAssertion.__tablename__):
            for file_id in file_ids_pending_extraction(db, source.id):
                file = db.get(File, file_id)
                if file is not None:
                    mark_extraction_requested(file)
        batch.status = "completed"
        batch.finished_at = datetime.now(UTC)
        batch.stats = stats
        record_event(
            db,
            "import.folder_completed",
            actor_user_id=actor.id,
            entity_type="import_batch",
            entity_id=str(batch_id),
            details={"source_id": str(source.id), "stats": stats},
        )
        db.commit()
    except Exception:
        # A catastrophic (non-per-file) error: finalize the durable batch row as failed in its own
        # commit so the failure stays visible, then re-raise.
        db.rollback()
        batch.status = "failed"
        batch.finished_at = datetime.now(UTC)
        batch.stats = stats
        db.commit()
        raise
    db.refresh(batch)
    return batch


def _source_root(source: Source) -> Path:
    config = source.config or {}
    raw_root = config.get("root_path")
    if not raw_root:
        raise ValueError("Server-folder source has no configured root path")
    root = Path(str(raw_root)).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError("Server-folder source root is not available")
    return root


def _assert_inside_root(root: Path, path: Path) -> None:
    path.resolve().relative_to(root.resolve())


def _as_utc(value: datetime) -> datetime:
    """Treat a stored (possibly naive) timestamp as UTC for comparison."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _existing_location(db: Session, source_id: uuid.UUID, pdf_path: Path) -> Location | None:
    return db.scalar(
        select(Location).where(
            Location.source_id == source_id, Location.internal_uri == str(pdf_path)
        )
    )


def _import_pdf_path(
    db: Session,
    *,
    source: Source,
    pdf_path: Path,
    import_batch_id: uuid.UUID | None = None,
) -> dict[str, bool]:
    now = datetime.now(UTC)

    # Incremental scan (E7): if this path was already imported and the file hasn't been modified
    # since we last verified it, skip the expensive SHA-256 + PDF preview and just refresh liveness.
    existing_loc = _existing_location(db, source.id, pdf_path)
    if existing_loc is not None and existing_loc.last_verified_at is not None:
        mtime = datetime.fromtimestamp(pdf_path.stat().st_mtime, tz=UTC)
        if mtime <= _as_utc(existing_loc.last_verified_at):
            existing_loc.last_verified_at = now
            existing_loc.is_available = True
            file = db.get(File, existing_loc.file_id)
            if file is not None:
                file.last_seen_at = now
                file.status = "available"
            return {"created_file": False, "created_work": False}

    sha256 = _sha256_file(pdf_path)
    file = db.scalar(select(File).where(File.sha256 == sha256))
    created_file = file is None
    preview = _extract_pdf_preview(pdf_path)

    if file is None:
        # SAVEPOINT so a concurrent import racing on the unique sha256 loses gracefully: roll back
        # just this insert and reuse the winner's row instead of failing the whole batch.
        try:
            with db.begin_nested():
                file = File(
                    sha256=sha256,
                    size_bytes=pdf_path.stat().st_size,
                    mime_type="application/pdf",
                    original_filename=pdf_path.name,
                    page_count=preview.get("page_count"),
                    text_layer_quality=preview.get("text_layer_quality", "unknown"),
                    status="available",
                    preview_text=preview.get("preview_text"),
                    last_seen_at=now,
                )
                db.add(file)
        except IntegrityError:
            file = db.scalar(select(File).where(File.sha256 == sha256))
            if file is None:
                raise
            created_file = False
    if not created_file:
        file.last_seen_at = now
        file.status = "available"
        if not file.preview_text and preview.get("preview_text"):
            file.preview_text = preview.get("preview_text")
        if file.page_count is None and preview.get("page_count") is not None:
            file.page_count = preview.get("page_count")

    if not _location_exists(db, file.id, source.id, pdf_path):
        db.add(
            Location(
                file_id=file.id,
                source_id=source.id,
                location_type="server_path",
                display_path=pdf_path.name,
                internal_uri=str(pdf_path),
                path_alias=source.path_alias,
                is_available=True,
                is_primary=True,
                last_verified_at=now,
            )
        )
    elif existing_loc is not None:
        # Same path already located: refresh verification time so a later unchanged scan skips it.
        existing_loc.last_verified_at = now
        existing_loc.is_available = True

    created_work = False
    if created_file:
        title = _title_from_filename(pdf_path)
        raw_arxiv_id = _arxiv_id_from_filename(pdf_path)
        work = Work(
            canonical_title=title,
            normalized_title=normalize_title(title),
            canonical_metadata_source="filename",
            arxiv_id=raw_arxiv_id,
            arxiv_base_id=_arxiv_base_id(raw_arxiv_id),
            import_batch_id=import_batch_id,
        )
        db.add(work)
        db.flush()
        db.add(FileWorkLink(file_id=file.id, work_id=work.id, user_confirmed=False))
        created_work = True

    return {"created_file": created_file, "created_work": created_work}


def _location_exists(db: Session, file_id: uuid.UUID, source_id: uuid.UUID, pdf_path: Path) -> bool:
    return (
        db.scalar(
            select(Location.id).where(
                Location.file_id == file_id,
                Location.source_id == source_id,
                Location.internal_uri == str(pdf_path),
            )
        )
        is not None
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_pdf_preview(path: Path) -> dict[str, Any]:
    try:
        import fitz  # type: ignore[import-not-found]
    except ImportError:
        return {"page_count": None, "preview_text": None, "text_layer_quality": "unknown"}

    try:
        with fitz.open(path) as document:
            page_count = document.page_count
            preview_text = ""
            if page_count:
                preview_text = document.load_page(0).get_text("text").strip()
            quality = "good" if preview_text else "none"
            return {
                "page_count": page_count,
                "preview_text": preview_text[:8000] or None,
                "text_layer_quality": quality,
            }
    except Exception:
        return {"page_count": None, "preview_text": None, "text_layer_quality": "unknown"}


def probe_pdf_openable(pdf_bytes: bytes) -> str | None:
    """Parser-level upload validation (AUDIT E2): return an error message, or ``None`` if OK.

    The upload handlers already check the size cap and the ``%PDF`` magic bytes; those catch
    truncated/non-PDF junk but not a well-formed header wrapping bytes GROBID/OCR cannot process.
    This opens the bytes with PyMuPDF and **fails closed** so encrypted or structurally broken PDFs
    are rejected at upload time with a clear reason rather than failing deep inside a worker.

    Unlike :func:`_extract_pdf_preview` (which fails *open* to "unknown" for quality classification),
    a non-``None`` return here means "reject this upload". If PyMuPDF is unavailable the probe is
    skipped (returns ``None``) — it is present in every shipped backend image, so this only affects
    stripped environments where the existing header check remains the guard.
    """
    try:
        import fitz  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as document:
            if document.needs_pass:
                return "The PDF is encrypted or password-protected and cannot be processed."
            if document.page_count < 1:
                return "The PDF has no pages."
            # Force a real parse of the first page; a malformed page tree raises here.
            document.load_page(0)
    except Exception:  # noqa: BLE001 - any open/parse failure is a rejection reason
        return "The PDF could not be opened; it may be corrupt or in an unsupported format."
    return None


def _title_from_filename(path: Path) -> str:
    return path.stem.replace("_", " ").replace("-", " ").strip() or path.name


def _arxiv_id_from_filename(path: Path) -> str | None:
    """Return the arXiv id if the filename is an arXiv id (e.g. 1706.03762.pdf)."""
    match = _ARXIV_ID_RE.match(path.stem.strip())
    return match.group(1) if match else None


def _ensure_managed_file(
    db: Session, *, filename: str, pdf_bytes: bytes, settings: Settings
) -> tuple[File, bool]:
    """Store PDF bytes content-addressed under the managed root and ensure File + Location.

    Returns ``(file, created_file)``. Shared by upload-as-new-work
    (:func:`import_uploaded_pdf`) and attach-to-existing-work
    (:func:`attach_uploaded_pdf_to_work`); dedups by SHA-256.
    """
    sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    managed_root = Path(settings.managed_library_root).expanduser().resolve()
    managed_root.mkdir(parents=True, exist_ok=True)
    dest = content_addressed_path(managed_root, sha256)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        dest.write_bytes(pdf_bytes)

    now = datetime.now(UTC)
    file = db.scalar(select(File).where(File.sha256 == sha256))
    created_file = file is None

    if file is None:
        preview = _extract_pdf_preview(dest)
        safe_name = Path(filename).name or "upload.pdf"
        # SAVEPOINT so a concurrent upload racing on the unique sha256 loses gracefully: roll back
        # just this insert and reuse the winner's row instead of failing the request.
        try:
            with db.begin_nested():
                file = File(
                    sha256=sha256,
                    size_bytes=len(pdf_bytes),
                    mime_type="application/pdf",
                    original_filename=safe_name,
                    page_count=preview.get("page_count"),
                    text_layer_quality=preview.get("text_layer_quality", "unknown"),
                    status="available",
                    preview_text=preview.get("preview_text"),
                    last_seen_at=now,
                )
                db.add(file)
        except IntegrityError:
            file = db.scalar(select(File).where(File.sha256 == sha256))
            if file is None:
                raise
            created_file = False
    if not created_file:
        file.last_seen_at = now

    # Location pointing to the managed store.
    if not db.scalar(
        select(Location.id).where(
            Location.file_id == file.id,
            Location.location_type == "managed_path",
        )
    ):
        db.add(
            Location(
                file_id=file.id,
                source_id=None,
                location_type="managed_path",
                display_path=Path(filename).name,
                internal_uri=str(dest),
                is_available=True,
                is_primary=True,
                last_verified_at=now,
            )
        )
    return file, created_file


def import_uploaded_pdf(
    db: Session,
    *,
    filename: str,
    pdf_bytes: bytes,
    actor: User,
    settings: Settings | None = None,
) -> tuple[ImportBatch, File, bool]:
    """Register an uploaded PDF in the managed library as a new work.

    The file is stored content-addressed under ``managed_library_root`` so uploads are
    automatically deduplicated by SHA-256.  Returns ``(batch, file, created_file)``;
    ``created_file`` is False when the exact same file already existed.
    """
    if settings is None:
        settings = get_settings()

    now = datetime.now(UTC)
    file, created_file = _ensure_managed_file(
        db, filename=filename, pdf_bytes=pdf_bytes, settings=settings
    )

    # Create the batch before the work so the work can carry its import_batch_id (Phase B6).
    batch = ImportBatch(
        created_by_user_id=actor.id,
        source_id=None,
        input_type="upload",
        status="complete",
        started_at=now,
        finished_at=now,
        stats={
            "files_scanned": 1,
            "files_created": int(created_file),
            "works_created": int(created_file),
            "duplicates_skipped": int(not created_file),
        },
    )
    db.add(batch)
    db.flush()

    if created_file:
        title = _title_from_filename(Path(filename))
        raw_arxiv_id = _arxiv_id_from_filename(Path(filename))
        work = Work(
            canonical_title=title,
            normalized_title=normalize_title(title),
            canonical_metadata_source="filename",
            arxiv_id=raw_arxiv_id,
            arxiv_base_id=_arxiv_base_id(raw_arxiv_id),
            import_batch_id=batch.id,
        )
        db.add(work)
        db.flush()
        db.add(FileWorkLink(file_id=file.id, work_id=work.id, user_confirmed=False))

    record_event(
        db,
        "import.upload",
        actor_user_id=actor.id,
        entity_type="file",
        entity_id=str(file.id),
        details={"filename": Path(filename).name, "sha256_prefix": file.sha256[:8]},
    )
    return batch, file, created_file


def stage_managed_file(
    db: Session, *, filename: str, pdf_bytes: bytes, settings: Settings | None = None
) -> tuple[File, bool]:
    """Store PDF bytes content-addressed and ensure a File + Location, WITHOUT minting a Work.

    Used by the multi-PDF staging import (batch10 #1): the PDF is persisted immediately (dedup-safe
    by SHA-256) so it can be extracted for preview and later linked to a Work on commit — but no
    ``Work``/``FileWorkLink`` is created here. Returns ``(file, created_file)``.
    """
    return _ensure_managed_file(
        db, filename=filename, pdf_bytes=pdf_bytes, settings=settings or get_settings()
    )


def attach_uploaded_pdf_to_work(
    db: Session,
    *,
    work: Work,
    filename: str,
    pdf_bytes: bytes,
    actor: User,
    settings: Settings | None = None,
) -> tuple[File, bool, bool]:
    """Store an uploaded PDF and link it to an *existing* work (not a new one).

    Lets a manually-created work gain a PDF.  Returns ``(file, created_file, newly_linked)``;
    ``newly_linked`` is False when this file was already attached to the work.
    """
    if settings is None:
        settings = get_settings()

    file, created_file = _ensure_managed_file(
        db, filename=filename, pdf_bytes=pdf_bytes, settings=settings
    )
    existing_link = db.scalar(
        select(FileWorkLink).where(FileWorkLink.file_id == file.id, FileWorkLink.work_id == work.id)
    )
    newly_linked = existing_link is None
    if newly_linked:
        db.add(FileWorkLink(file_id=file.id, work_id=work.id, user_confirmed=True))
    record_event(
        db,
        "work.file_attached",
        actor_user_id=actor.id,
        entity_type="work",
        entity_id=str(work.id),
        details={"file_id": str(file.id), "filename": Path(filename).name},
    )
    return file, created_file, newly_linked
