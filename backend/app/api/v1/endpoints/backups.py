"""Backup/export + restore endpoints (feature batch 2026-07-13, item 1).

Creating/listing/downloading a backup needs **admin**; uploading an archive and restoring —
which can rewrite the whole database — is **owner-only**, with an explicit typed confirmation
for replace mode. Restores are enqueued on the worker (falling back to inline when Redis is
down) and their summaries are read back from the audit trail.
"""

from __future__ import annotations

import re
import shutil
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_admin, require_owner
from app.db.session import get_db
from app.models.audit import AuditEvent
from app.models.user import User
from app.services import backup as backup_service
from app.services.audit import record_event

router = APIRouter()
DB_DEP = Depends(get_db)
ADMIN_DEP = Depends(require_admin)
OWNER_DEP = Depends(require_owner)

_ARCHIVE_NAME = re.compile(r"^[A-Za-z0-9._-]+\.zip$")


def _safe_archive_path(name: str):
    """Resolve an archive name inside the backups dir, refusing traversal/odd names."""
    if not _ARCHIVE_NAME.match(name):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Bad archive name")
    path = backup_service.backups_dir() / name
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backup not found")
    return path


class BackupEntry(BaseModel):
    archive: str
    size_bytes: int
    created_at: datetime


class BackupsOut(BaseModel):
    backups: list[BackupEntry]
    last_restore: dict | None = None


class BackupCreateIn(BaseModel):
    include_pdfs: bool = False


class BackupCreateOut(BaseModel):
    queued: bool
    job_id: str | None = None
    # Filled when the queue was unavailable and the export ran inline.
    archive: str | None = None


class RestoreIn(BaseModel):
    mode: Literal["merge", "replace"]
    # A configured import-root alias whose folder is scanned (sha256) for PDFs to pair.
    pdf_root_alias: str | None = None
    # Replace mode rewrites the whole database — require the word typed back.
    confirm: str | None = None


class RestoreOut(BaseModel):
    queued: bool
    job_id: str | None = None
    summary: dict | None = None


@router.get("", response_model=BackupsOut)
def list_backups(db: Session = DB_DEP, _admin: User = ADMIN_DEP) -> BackupsOut:
    """List the archives in the backups folder + the last restore summary (from the audit log)."""
    entries = []
    for path in sorted(backup_service.backups_dir().glob("*.zip"), reverse=True):
        stat = path.stat()
        entries.append(
            BackupEntry(
                archive=path.name,
                size_bytes=stat.st_size,
                created_at=datetime.fromtimestamp(stat.st_mtime).astimezone(),
            )
        )
    last = db.scalars(
        select(AuditEvent)
        .where(AuditEvent.event_type == "backup.restored")
        .order_by(AuditEvent.created_at.desc())
    ).first()
    last_restore = None
    if last is not None:
        last_restore = {"at": last.created_at.isoformat(), **(last.details or {})}
    return BackupsOut(backups=entries, last_restore=last_restore)


@router.post("", response_model=BackupCreateOut, status_code=status.HTTP_202_ACCEPTED)
def create_backup_endpoint(
    payload: BackupCreateIn, db: Session = DB_DEP, actor: User = ADMIN_DEP
) -> BackupCreateOut:
    """Create a backup archive (admin). Runs on the worker; inline when the queue is down."""
    from app.workers.queue import enqueue_backup_export

    job_id = enqueue_backup_export(include_pdfs=payload.include_pdfs, actor_user_id=str(actor.id))
    if job_id is not None:
        return BackupCreateOut(queued=True, job_id=job_id)
    result = backup_service.create_backup(
        db, include_pdfs=payload.include_pdfs, actor_user_id=actor.id
    )
    db.commit()
    return BackupCreateOut(queued=False, archive=result["archive"])


@router.get("/{name}/download")
def download_backup(name: str, _admin: User = ADMIN_DEP) -> FileResponse:
    """Download a backup archive (admin)."""
    path = _safe_archive_path(name)
    return FileResponse(path, media_type="application/zip", filename=name)


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_backup(name: str, db: Session = DB_DEP, actor: User = ADMIN_DEP) -> None:
    """Delete a backup archive (admin, audited)."""
    path = _safe_archive_path(name)
    path.unlink()
    record_event(db, "backup.deleted", actor_user_id=actor.id, entity_type="backup", entity_id=name)
    db.commit()


@router.get("/{name}/analyze")
def analyze_backup_endpoint(name: str, _owner: User = OWNER_DEP) -> dict:
    """Dry-run compatibility report for an archive against the CURRENT schema (owner).

    Shows which tables/columns of the backup would be dropped and which current columns will be
    backfilled — the "what is about to happen" step before a restore.
    """
    try:
        return backup_service.analyze_backup(_safe_archive_path(name))
    except Exception as exc:  # noqa: BLE001 - a corrupt zip must answer 400, not 500
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unreadable archive: {exc}"
        ) from exc


@router.post("/upload")
def upload_backup(upload: UploadFile, db: Session = DB_DEP, actor: User = OWNER_DEP) -> dict:
    """Upload a backup archive into the backups folder (owner) and return its analyze report."""
    name = f"uploaded-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.zip"
    path = backup_service.backups_dir() / name
    with path.open("wb") as out:
        shutil.copyfileobj(upload.file, out)
    try:
        report = backup_service.analyze_backup(path)
    except Exception as exc:  # noqa: BLE001 - reject a non-backup upload outright
        path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Not a readable backup archive: {exc}"
        ) from exc
    record_event(
        db, "backup.uploaded", actor_user_id=actor.id, entity_type="backup", entity_id=name
    )
    db.commit()
    return {"archive": name, **report}


@router.post("/{name}/restore", response_model=RestoreOut, status_code=status.HTTP_202_ACCEPTED)
def restore_backup_endpoint(
    name: str, payload: RestoreIn, db: Session = DB_DEP, actor: User = OWNER_DEP
) -> RestoreOut:
    """Restore an archive (OWNER ONLY). ``merge`` adds missing rows; ``replace`` rewrites the DB.

    Replace mode requires ``confirm == "REPLACE"`` (typed by the owner in the UI). The heavy work
    runs on the worker; the summary lands in the audit log / the backups list.
    """
    path = _safe_archive_path(name)
    if payload.mode == "replace" and payload.confirm != "REPLACE":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Replace mode rewrites the whole database — send confirm="REPLACE"',
        )
    from app.workers.queue import enqueue_backup_restore

    record_event(
        db,
        "backup.restore_requested",
        actor_user_id=actor.id,
        entity_type="backup",
        entity_id=name,
        details={"mode": payload.mode, "pdf_root_alias": payload.pdf_root_alias},
    )
    db.commit()
    job_id = enqueue_backup_restore(
        archive=name,
        mode=payload.mode,
        pdf_root_alias=payload.pdf_root_alias,
        actor_user_id=str(actor.id),
    )
    if job_id is not None:
        return RestoreOut(queued=True, job_id=job_id)
    summary = backup_service.restore_backup(
        db,
        path=path,
        mode=payload.mode,
        pdf_root_alias=payload.pdf_root_alias,
        actor_user_id=actor.id,
    )
    db.commit()
    return RestoreOut(queued=False, summary=summary)
