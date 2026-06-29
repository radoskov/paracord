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
    mime_type: str | None = None
    modified_at: str | None = None
    page_count: int | None = None


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
