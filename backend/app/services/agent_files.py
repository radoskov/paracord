"""Agent manifest ingestion and teleport (SPEC §11 / M5).

Security model: the server never holds or requests a server-usable path on the agent's
machine. The agent reports opaque file identity (``local_file_id``, sha256, size) via a
manifest. A *user* requests a teleport for one of those entries; the agent then **pushes** the
bytes, which the server verifies against the manifest hash before storing the managed file.
"""

import contextlib
import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.agent import Agent, AgentFile
from app.models.file import File, FileWorkLink
from app.models.metadata import MetadataAssertion
from app.models.work import Work
from app.services import storage
from app.services.audit import record_event
from app.services.default_shelf import place_on_default_if_loose
from app.services.file_paths import derived_ocr_path
from app.services.identifiers import arxiv_base_id
from app.utils.normalization import normalize_title


def _stub_title(item) -> str:
    """A filename-derived title for an index_only stub paper (B6)."""
    name = item.virtual_path or item.display_path or item.local_file_id
    return storage._title_from_filename(Path(name))


def ingest_manifest(db: Session, *, agent: Agent, items: list, create_stubs: bool = True) -> int:
    """Upsert the agent's manifest entries. Returns the number of items processed.

    Existing rows keep their teleport state; only the reported metadata is refreshed. A manifest
    over the configured ``max_batch_items`` cap is rejected (D1); the agent chunks large scans into
    ≤cap manifests, so a rejection here means an unchunked/oversized client push.

    B6: when ``create_stubs`` is on (the agent's default), an ``index_only`` entry also creates a
    minimal library "stub" paper (filename title, no PDF, ``source='agent_index_only'``) linked via
    ``AgentFile.work_id`` — visible in the library and promotable later via Extract/Teleport. The
    link makes it idempotent: a re-scan never creates a second stub.
    """
    from app.services.app_config import enforce_batch_limit

    enforce_batch_limit(db, len(items))
    now = datetime.now(UTC)
    stubs_created = 0
    for item in items:
        row = db.scalar(
            select(AgentFile).where(
                AgentFile.agent_id == agent.id, AgentFile.local_file_id == item.local_file_id
            )
        )
        if row is None:
            row = AgentFile(
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
            db.add(row)
        else:
            row.sha256 = item.sha256
            row.size_bytes = item.size_bytes
            row.display_path = item.display_path
            row.virtual_path = item.virtual_path or item.display_path
            row.mime_type = item.mime_type
            row.import_action = item.import_action
            row.teleport_policy = item.teleport_policy
            # A re-indexed file that had been marked source-removed is present again.
            if row.processing_state == "source_removed":
                row.processing_state = "indexed"
            row.updated_at = now
        # B6: create a promotable stub for an index_only entry that doesn't already have one.
        if create_stubs and item.import_action == "index_only" and row.work_id is None:
            title = _stub_title(item)
            work = Work(
                canonical_title=title,
                normalized_title=normalize_title(title),
                # Origin marker the UI badges as "not extracted"; cleared on extract/teleport.
                canonical_metadata_source="agent_index_only",
                created_by_user_id=agent.created_by_user_id,
            )
            db.add(work)
            db.flush()
            row.work_id = work.id
            place_on_default_if_loose(db, work.id, actor_id=agent.created_by_user_id)
            stubs_created += 1
    record_event(
        db,
        "agent.manifest_received",
        entity_type="agent",
        entity_id=str(agent.id),
        details={"item_count": len(items), "stubs_created": stubs_created},
    )
    return len(items)


def extracted_metadata_for(
    db: Session, agent_files: list[AgentFile]
) -> dict[uuid.UUID, tuple[str | None, str | None]]:
    """Resolve (title, authors) of each agent file's linked Work, keyed by AgentFile.id (#11).

    Title is the linked Work's ``canonical_title``; authors is its best ``authors`` assertion
    (canonical first, then confidence). Resolved in two batched queries to avoid an N+1.
    """
    by_file_id = {af.file_id: af for af in agent_files if af.file_id is not None}
    if not by_file_id:
        return {}
    # file_id → work (one query); a file may link to several works — first link wins.
    work_rows = db.execute(
        select(FileWorkLink.file_id, Work.id, Work.canonical_title)
        .join(Work, Work.id == FileWorkLink.work_id)
        .where(FileWorkLink.file_id.in_(by_file_id.keys()))
    ).all()
    file_to_work: dict[uuid.UUID, tuple[uuid.UUID, str | None]] = {}
    for file_id, work_id, title in work_rows:
        file_to_work.setdefault(file_id, (work_id, title))
    work_ids = [wid for wid, _ in file_to_work.values()]
    authors_by_work: dict[uuid.UUID, str] = {}
    if work_ids:
        rows = db.execute(
            select(MetadataAssertion.entity_id, MetadataAssertion.value)
            .where(
                MetadataAssertion.entity_type == "work",
                MetadataAssertion.entity_id.in_(work_ids),
                MetadataAssertion.field_name == "authors",
            )
            .order_by(
                MetadataAssertion.entity_id,
                MetadataAssertion.selected_as_canonical.desc(),
                func.coalesce(MetadataAssertion.confidence, 0).desc(),
            )
        ).all()
        for entity_id, value in rows:
            authors_by_work.setdefault(entity_id, value)
    out: dict[uuid.UUID, tuple[str | None, str | None]] = {}
    for file_id, agent_file in by_file_id.items():
        work = file_to_work.get(file_id)
        if work is None:
            continue
        work_id, title = work
        out[agent_file.id] = (title, authors_by_work.get(work_id))
    return out


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
    # Ensure a linked Work exists (mirrors upload) — not only on first creation, so a re-teleport
    # of a file whose work was removed still has something to attach extraction to.
    has_link = db.scalar(select(FileWorkLink.id).where(FileWorkLink.file_id == file.id)) is not None
    if not has_link:
        # B6: teleporting a previously index_only file enriches its existing stub paper rather than
        # creating a duplicate — attach the uploaded file and clear the "index_only" origin marker.
        stub = db.get(Work, agent_file.work_id) if agent_file.work_id else None
        if stub is not None:
            stub.canonical_metadata_source = "teleport"  # clears the "not extracted" marker
            work = stub
        else:
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
        place_on_default_if_loose(
            db, work.id, actor_id=agent_file.requested_by_user_id
        )  # no free-floating papers (#1)

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


def offer_teleport(
    db: Session,
    *,
    agent: Agent,
    local_file_id: str,
    pdf_bytes: bytes,
    settings: Settings | None = None,
    display_path: str | None = None,
) -> File:
    """Agent-*initiated* teleport (#12): the agent pushes a file without a prior user request.

    Allowed only when the owner has granted ``can_teleport`` (enforced at the endpoint). Creates
    the manifest entry if it doesn't exist yet (so a file can be offered without a separate sync),
    verifies the bytes against the reported/manifested hash, stores the managed file + Work, and
    records ``teleport.completed`` with ``initiated_by='agent'``.
    """
    if settings is None:
        settings = get_settings()
    actual = hashlib.sha256(pdf_bytes).hexdigest()
    agent_file = db.scalar(
        select(AgentFile).where(
            AgentFile.agent_id == agent.id, AgentFile.local_file_id == local_file_id
        )
    )
    if agent_file is None:
        agent_file = AgentFile(
            agent_id=agent.id,
            local_file_id=local_file_id,
            sha256=actual,
            size_bytes=len(pdf_bytes),
            display_path=display_path,
            virtual_path=display_path,
            import_action="teleport",
        )
        db.add(agent_file)
        db.flush()
    if agent_file.teleport_blocked:
        raise ValueError("File is blocked from teleport by the agent")
    if actual != agent_file.sha256:
        agent_file.teleport_status = "failed"
        record_event(
            db,
            "teleport.failed",
            entity_type="agent",
            entity_id=str(agent.id),
            details={
                "local_file_id": local_file_id,
                "reason": "sha256 mismatch",
                "initiated_by": "agent",
            },
        )
        raise ValueError("Uploaded content does not match the manifested SHA-256")

    name = Path(agent_file.display_path or display_path or local_file_id).name or "teleport.pdf"
    file, _created_file = storage._ensure_managed_file(
        db, filename=name, pdf_bytes=pdf_bytes, settings=settings
    )
    has_link = db.scalar(select(FileWorkLink.id).where(FileWorkLink.file_id == file.id)) is not None
    if not has_link:
        # B6: an agent-initiated teleport of a previously index_only file must enrich its existing
        # stub paper rather than create a duplicate (mirrors complete_teleport) — reuse the linked
        # stub Work when present, only creating a fresh Work when the file was never scanned.
        stub = db.get(Work, agent_file.work_id) if agent_file.work_id else None
        if stub is not None:
            stub.canonical_metadata_source = "teleport"  # clears the "not extracted" marker
            work = stub
        else:
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
        place_on_default_if_loose(db, work.id)  # no free-floating papers (#1)

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
            "initiated_by": "agent",
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
    # Ensure a linked Work exists — not just when the File is newly created. On a **re-extract**
    # the File already exists (same sha256), and its prior Work may have been deleted or the PDF
    # discarded; without this the extraction worker fails with "File has no linked work".
    has_link = db.scalar(select(FileWorkLink.id).where(FileWorkLink.file_id == file.id)) is not None
    if not has_link:
        # B6: if this file was an index_only stub, enrich that existing paper instead of creating a
        # second one — attach the PDF to the stub and drop its "index_only" origin marker so it is
        # no longer badged as un-extracted (extraction then fills its real metadata).
        stub = db.get(Work, agent_file.work_id) if agent_file.work_id else None
        if stub is not None:
            stub.canonical_metadata_source = "agent-extract"  # clears the "not extracted" marker
            work = stub
        else:
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
        place_on_default_if_loose(db, work.id)  # no free-floating papers (#1)

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

    if file.sha256:
        with contextlib.suppress(OSError, ValueError):
            derived_ocr_path(settings, file.sha256).unlink(missing_ok=True)

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
