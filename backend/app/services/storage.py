"""Managed library storage and server-folder import service."""

import hashlib
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
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

# New-style arXiv id, optionally with a version suffix (e.g. 2106.01345 / 1706.03762v5).
_ARXIV_ID_RE = re.compile(r"^(\d{4}\.\d{4,5})(v\d+)?$")


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
    """Return configured server-folder roots keyed by stable alias."""
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


def create_server_folder_source(
    db: Session,
    *,
    settings: Settings,
    name: str,
    path_alias: str,
    actor: User,
) -> Source:
    """Create a server-folder source from a configured root alias."""
    roots = configured_server_roots(settings)
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
    """Scan a configured server-folder source and import PDF file identities."""
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
        stats={"seen": 0, "created_files": 0, "created_works": 0, "existing_files": 0},
    )
    db.add(batch)
    db.flush()

    stats = dict(batch.stats or {})
    try:
        pattern = "**/*.pdf" if recursive else "*.pdf"
        for pdf_path in sorted(root.glob(pattern)):
            if not pdf_path.is_file():
                continue
            _assert_inside_root(root, pdf_path)
            stats["seen"] += 1
            result = _import_pdf_path(db, source=source, pdf_path=pdf_path)
            stats["created_files"] += int(result["created_file"])
            stats["created_works"] += int(result["created_work"])
            stats["existing_files"] += int(not result["created_file"])
        batch.status = "completed"
    except Exception:
        batch.status = "failed"
        batch.finished_at = datetime.now(UTC)
        batch.stats = stats
        raise

    batch.finished_at = datetime.now(UTC)
    batch.stats = stats
    record_event(
        db,
        "import.folder_completed",
        actor_user_id=actor.id,
        entity_type="import_batch",
        entity_id=str(batch.id),
        details={"source_id": str(source.id), "stats": stats},
    )
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


def _import_pdf_path(db: Session, *, source: Source, pdf_path: Path) -> dict[str, bool]:
    sha256 = _sha256_file(pdf_path)
    file = db.scalar(select(File).where(File.sha256 == sha256))
    created_file = file is None
    preview = _extract_pdf_preview(pdf_path)
    now = datetime.now(UTC)

    if file is None:
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
        db.flush()
    else:
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
        db.flush()
    else:
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

    if created_file:
        title = _title_from_filename(Path(filename))
        raw_arxiv_id = _arxiv_id_from_filename(Path(filename))
        work = Work(
            canonical_title=title,
            normalized_title=normalize_title(title),
            canonical_metadata_source="filename",
            arxiv_id=raw_arxiv_id,
            arxiv_base_id=_arxiv_base_id(raw_arxiv_id),
        )
        db.add(work)
        db.flush()
        db.add(FileWorkLink(file_id=file.id, work_id=work.id, user_confirmed=False))

    batch = ImportBatch(
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
    record_event(
        db,
        "import.upload",
        actor_user_id=actor.id,
        entity_type="file",
        entity_id=str(file.id),
        details={"filename": Path(filename).name, "sha256_prefix": file.sha256[:8]},
    )
    return batch, file, created_file


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
