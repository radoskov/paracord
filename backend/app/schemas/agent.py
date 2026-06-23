"""Local agent protocol schemas."""

from pydantic import BaseModel, Field


class AgentRegisterRequest(BaseModel):
    agent_name: str
    bootstrap_token: str


class AgentRegisterResponse(BaseModel):
    agent_id: str
    agent_token: str


class AgentFileManifestItem(BaseModel):
    local_file_id: str
    display_path: str
    sha256: str
    size_bytes: int
    modified_at: str | None = None
    page_count: int | None = None


class AgentManifestRequest(BaseModel):
    agent_id: str
    files: list[AgentFileManifestItem] = Field(default_factory=list)
