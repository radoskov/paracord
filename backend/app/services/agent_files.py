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
                    mime_type=item.mime_type,
                )
            )
        else:
            existing.sha256 = item.sha256
            existing.size_bytes = item.size_bytes
            existing.display_path = item.display_path
            existing.mime_type = item.mime_type
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
