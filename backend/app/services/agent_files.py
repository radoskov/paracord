"""Agent manifest ingestion and teleport (SPEC §11 / M5).

Security model: the server never holds or requests a server-usable path on the agent's
machine. The agent reports opaque file identity (``local_file_id``, sha256, size) via a
manifest. A *user* requests a teleport for one of those entries; the agent then **pushes** the
bytes, which the server verifies against the manifest hash before storing the managed file.
"""

import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.agent import Agent, AgentFile
from app.models.file import File, FileWorkLink
from app.models.work import Work
from app.services import storage
from app.services.audit import record_event
from app.services.identifiers import arxiv_base_id
from app.utils.normalization import normalize_title


def ingest_manifest(db: Session, *, agent: Agent, items: list) -> int:
    """Upsert the agent's manifest entries. Returns the number of items processed.

    Existing rows keep their teleport state; only the reported metadata is refreshed.
    """
    now = datetime.now(UTC)
    for item in items:
        existing = db.scalar(
            select(AgentFile).where(
                AgentFile.agent_id == agent.id, AgentFile.local_file_id == item.local_file_id
            )
        )
        if existing is None:
            db.add(
                AgentFile(
                    agent_id=agent.id,
                    local_file_id=item.local_file_id,
                    sha256=item.sha256,
                    size_bytes=item.size_bytes,
                    display_path=item.display_path,
                    virtual_path=item.virtual_path or item.display_path,
                    mime_type=item.mime_type,
                    import_action=item.import_action,
                    teleport_policy=item.teleport_policy,
                )
            )
        else:
            existing.sha256 = item.sha256
            existing.size_bytes = item.size_bytes
            existing.display_path = item.display_path
            existing.virtual_path = item.virtual_path or item.display_path
            existing.mime_type = item.mime_type
            existing.import_action = item.import_action
            existing.teleport_policy = item.teleport_policy
            # A re-indexed file that had been marked source-removed is present again.
            if existing.processing_state == "source_removed":
                existing.processing_state = "indexed"
            existing.updated_at = now
    record_event(
        db,
        "agent.manifest_received",
        entity_type="agent",
        entity_id=str(agent.id),
        details={"item_count": len(items)},
    )
    return len(items)


def request_teleport(
    db: Session, *, agent_id: uuid.UUID, local_file_id: str, requested_by
) -> AgentFile:
    """Mark a manifest entry as teleport-requested (user-authorised)."""
    agent_file = db.scalar(
        select(AgentFile).where(
            AgentFile.agent_id == agent_id, AgentFile.local_file_id == local_file_id
        )
    )
    if agent_file is None:
        raise ValueError("No such agent file in any manifest")
    if agent_file.teleport_status == "complete":
        raise ValueError("File has already been teleported")
    if agent_file.teleport_blocked:
        raise ValueError("File is blocked from teleport by the agent")
    agent_file.teleport_status = "requested"
    agent_file.requested_by_user_id = getattr(requested_by, "id", None)
    record_event(
        db,
        "teleport.requested",
        actor_user_id=getattr(requested_by, "id", None),
        entity_type="agent",
        entity_id=str(agent_id),
        details={"local_file_id": local_file_id},
    )
    return agent_file


def pending_teleports(db: Session, *, agent: Agent) -> list[AgentFile]:
    """Return the agent's files awaiting an upload (teleport requested, not yet complete)."""
    return list(
        db.scalars(
            select(AgentFile).where(
                AgentFile.agent_id == agent.id, AgentFile.teleport_status == "requested"
            )
        ).all()
    )


def complete_teleport(
    db: Session,
    *,
    agent: Agent,
    local_file_id: str,
    pdf_bytes: bytes,
    settings: Settings | None = None,
) -> File:
    """Verify pushed bytes against the manifest hash, then store the managed file + work.

    Raises ``ValueError`` (recording ``teleport.failed``) if the entry is unknown or the bytes
    don't match the manifest sha256.
    """
    if settings is None:
        settings = get_settings()
    agent_file = db.scalar(
        select(AgentFile).where(
            AgentFile.agent_id == agent.id, AgentFile.local_file_id == local_file_id
        )
    )
    if agent_file is None:
        raise ValueError("No such agent file in this agent's manifest")

    actual = hashlib.sha256(pdf_bytes).hexdigest()
    if actual != agent_file.sha256:
        agent_file.teleport_status = "failed"
        record_event(
            db,
            "teleport.failed",
            entity_type="agent",
            entity_id=str(agent.id),
            details={"local_file_id": local_file_id, "reason": "sha256 mismatch"},
        )
        raise ValueError("Uploaded content does not match the manifested SHA-256")

    name = Path(agent_file.display_path or local_file_id).name or "teleport.pdf"
    file, created_file = storage._ensure_managed_file(
        db, filename=name, pdf_bytes=pdf_bytes, settings=settings
    )
    # Give a freshly-teleported file a work to attach extraction to (mirrors upload).
    if created_file:
        title = storage._title_from_filename(Path(name))
        raw_arxiv = storage._arxiv_id_from_filename(Path(name))
        work = Work(
            canonical_title=title,
            normalized_title=normalize_title(title),
            canonical_metadata_source="teleport",
            arxiv_id=raw_arxiv,
            arxiv_base_id=arxiv_base_id(raw_arxiv),
        )
        db.add(work)
        db.flush()
        db.add(FileWorkLink(file_id=file.id, work_id=work.id, user_confirmed=False))

    agent_file.teleport_status = "complete"
    agent_file.processing_state = "teleported"
    agent_file.file_id = file.id
    record_event(
        db,
        "teleport.completed",
        entity_type="agent",
        entity_id=str(agent.id),
        details={
            "local_file_id": local_file_id,
            "file_id": str(file.id),
            "sha256_prefix": actual[:8],
        },
    )
    return file


def _agent_file(db: Session, *, agent: Agent, local_file_id: str) -> AgentFile:
    agent_file = db.scalar(
        select(AgentFile).where(
            AgentFile.agent_id == agent.id, AgentFile.local_file_id == local_file_id
        )
    )
    if agent_file is None:
        raise ValueError("No such agent file in this agent's manifest")
    return agent_file


def reject_teleport(db: Session, *, agent: Agent, local_file_id: str, forever: bool) -> AgentFile:
    """Reject a pending teleport request; ``forever`` also blocks all future requests."""
    agent_file = _agent_file(db, agent=agent, local_file_id=local_file_id)
    agent_file.teleport_status = "rejected"
    if forever:
        agent_file.teleport_blocked = True
    record_event(
        db,
        "teleport.rejected",
        entity_type="agent",
        entity_id=str(agent.id),
        details={"local_file_id": local_file_id, "forever": forever},
    )
    return agent_file


def unblock_teleport(db: Session, *, agent: Agent, local_file_id: str) -> AgentFile:
    """Clear a reject-forever block so the file can be requested again."""
    agent_file = _agent_file(db, agent=agent, local_file_id=local_file_id)
    agent_file.teleport_blocked = False
    if agent_file.teleport_status == "rejected":
        agent_file.teleport_status = "none"
    return agent_file


def mark_source_removed(db: Session, *, agent: Agent, local_file_ids: list[str]) -> int:
    """Mark agent files whose source disappeared on the client (kept, flagged in the UI)."""
    count = 0
    for local_file_id in local_file_ids:
        agent_file = db.scalar(
            select(AgentFile).where(
                AgentFile.agent_id == agent.id, AgentFile.local_file_id == local_file_id
            )
        )
        # Only flag files not already permanently stored in the library.
        if agent_file is not None and agent_file.processing_state != "teleported":
            agent_file.processing_state = "source_removed"
            count += 1
    return count


def extract_and_index(
    db: Session,
    *,
    agent: Agent,
    local_file_id: str,
    pdf_bytes: bytes,
    settings: Settings | None = None,
) -> File:
    """`index_and_extract`: store the PDF transiently + create the Work, keep a preview, and flag it
    so the extraction worker discards the PDF afterwards (only the reference + metadata remain)."""
    if settings is None:
        settings = get_settings()
    agent_file = _agent_file(db, agent=agent, local_file_id=local_file_id)

    actual = hashlib.sha256(pdf_bytes).hexdigest()
    if actual != agent_file.sha256:
        agent_file.processing_state = "failed"
        record_event(
            db,
            "agent.extract_failed",
            entity_type="agent",
            entity_id=str(agent.id),
            details={"local_file_id": local_file_id, "reason": "sha256 mismatch"},
        )
        raise ValueError("Uploaded content does not match the manifested SHA-256")

    name = Path(agent_file.display_path or local_file_id).name or "extract.pdf"
    file, created_file = storage._ensure_managed_file(
        db, filename=name, pdf_bytes=pdf_bytes, settings=settings
    )
    agent_file.preview_text = (file.preview_text or "")[:2000] or None
    if created_file:
        title = storage._title_from_filename(Path(name))
        raw_arxiv = storage._arxiv_id_from_filename(Path(name))
        work = Work(
            canonical_title=title,
            normalized_title=normalize_title(title),
            canonical_metadata_source="agent-extract",
            arxiv_id=raw_arxiv,
            arxiv_base_id=arxiv_base_id(raw_arxiv),
        )
        db.add(work)
        db.flush()
        db.add(FileWorkLink(file_id=file.id, work_id=work.id, user_confirmed=False))

    agent_file.import_action = "index_and_extract"
    agent_file.processing_state = "extracting"
    agent_file.file_id = file.id
    record_event(
        db,
        "agent.extract_requested",
        entity_type="agent",
        entity_id=str(agent.id),
        details={"local_file_id": local_file_id, "file_id": str(file.id)},
    )
    return file


def discard_after_extract(db: Session, *, file: File, settings: Settings | None = None) -> bool:
    """If ``file`` was uploaded by an agent purely to extract (index_and_extract), delete the
    on-disk PDF + its managed location after extraction, keeping the Work, references and preview.

    Returns True if a discard happened. Called by the extraction worker post-extraction.
    """
    from app.models.file import Location

    if settings is None:
        settings = get_settings()
    agent_file = db.scalar(
        select(AgentFile).where(
            AgentFile.file_id == file.id, AgentFile.import_action == "index_and_extract"
        )
    )
    if agent_file is None or agent_file.processing_state != "extracting":
        return False

    for location in db.scalars(
        select(Location).where(
            Location.file_id == file.id, Location.location_type == "managed_path"
        )
    ).all():
        if location.internal_uri:
            try:
                path = Path(location.internal_uri)
                root = Path(settings.managed_library_root).expanduser().resolve()
                if path.expanduser().resolve().is_relative_to(root) and path.exists():
                    path.unlink()
            except (OSError, ValueError):
                pass
        db.delete(location)

    file.status = "extracted_discarded"
    agent_file.processing_state = "extracted"
    agent_file.file_id = None
    record_event(
        db,
        "agent.extract_discarded",
        entity_type="agent",
        entity_id=str(agent_file.agent_id),
        details={"local_file_id": agent_file.local_file_id, "file_id": str(file.id)},
    )
    return True
