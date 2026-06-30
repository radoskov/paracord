"""Local agent protocol schemas."""

import uuid

from pydantic import BaseModel, Field


class AgentRegisterRequest(BaseModel):
    agent_name: str
    bootstrap_token: str


class AgentRegisterResponse(BaseModel):
    agent_id: str
    agent_token: str


class AgentManifestItem(BaseModel):
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
    # The agent is identified by its bearer token, not a body field.
    items: list[AgentManifestItem] = Field(default_factory=list)


class PendingTeleportItem(BaseModel):
    local_file_id: str
    sha256: str
    display_path: str | None = None


class TeleportRequest(BaseModel):
    agent_id: uuid.UUID
    local_file_id: str


class RejectTeleportRequest(BaseModel):
    forever: bool = False


class AgentFileStatus(BaseModel):
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
