"""Local agent protocol schemas."""

import uuid

from pydantic import BaseModel, Field


class AgentRegisterRequest(BaseModel):
    """Body for a local agent to register itself using a one-time bootstrap token."""

    agent_name: str
    bootstrap_token: str


class AgentRegisterResponse(BaseModel):
    """Returned on successful registration: the agent's id and its long-lived bearer token."""

    agent_id: str
    agent_token: str


class AgentManifestItem(BaseModel):
    """One local file reported by an agent during a manifest sync."""

    local_file_id: str
    sha256: str
    size_bytes: int
    display_path: str | None = None
    virtual_path: str | None = None
    mime_type: str | None = None
    modified_at: str | None = None
    page_count: int | None = None
    import_action: str = "index_only"
    teleport_policy: str = "ask"


class AgentManifestRequest(BaseModel):
    """Batch of local files an agent is reporting/syncing to the server."""

    # The agent is identified by its bearer token, not a body field.
    items: list[AgentManifestItem] = Field(default_factory=list)
    # B6: whether index_only entries create a minimal library "stub" paper (the agent's
    # "Create library stubs for index-only files" toggle, default on). Older agents omit it → True.
    create_stubs: bool = True


class PendingTeleportItem(BaseModel):
    """A local file awaiting the user's approve/reject decision to be teleported to the server."""

    local_file_id: str
    sha256: str
    display_path: str | None = None


class TeleportRequest(BaseModel):
    """Request to pull one local file from a specific agent up to the server."""

    agent_id: uuid.UUID
    local_file_id: str


class RejectTeleportRequest(BaseModel):
    """Decline a pending teleport; ``forever`` also blocks future prompts for that file."""

    forever: bool = False


class AgentFileStatus(BaseModel):
    """Server-side view of one agent-reported file's import/teleport progress."""

    local_file_id: str
    virtual_path: str | None = None
    display_path: str | None = None
    import_action: str
    processing_state: str
    teleport_status: str
    teleport_policy: str
    teleport_blocked: bool
    # Server→agent metadata sync (#11): canonical title + authors of the linked Work, so the
    # agent GUI can search/sort by them. Populated by the file-status endpoint, not the model.
    extracted_title: str | None = None
    extracted_authors: str | None = None
