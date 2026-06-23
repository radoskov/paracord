"""Agent configuration."""

from pathlib import Path

from pydantic import BaseModel


class AgentConfig(BaseModel):
    """Local agent configuration."""

    name: str = "workstation-agent"
    server_url: str = "http://127.0.0.1:8000"
    allowed_roots: list[Path] = []
    follow_symlinks: bool = False
    teleport_enabled: bool = True
