"""Agent configuration (SPEC §32.2).

The agent owns a single, persistent, tool-managed config at ``~/.config/paracord/agent.yaml``
(override with ``$PARACORD_AGENT_HOME`` or an explicit path). It round-trips via pydantic, so the
CLI and web GUI can read and rewrite it. Secrets live separately (see ``secrets.py``).
"""

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

# Import action applied to a file: index_only | index_and_extract | teleport.
ACTIONS = ("index_only", "index_and_extract", "teleport")
# Whether the server may teleport without per-request approval: ask | allow.
POLICIES = ("ask", "allow")


class ManagedFolder(BaseModel):
    path: Path
    mode: str = "monitored"  # monitored | once
    action: str = "index_only"
    teleport_policy: str = "ask"
    enabled: bool = True  # paused folders are kept in config but skipped on scan


class ManagedFile(BaseModel):
    path: Path
    action: str = "index_only"
    teleport_policy: str = "ask"
    enabled: bool = True


class AgentConfig(BaseModel):
    """Persistent agent configuration."""

    name: str = "workstation-agent"
    server_url: str = "http://127.0.0.1:8000"
    agent_id: str | None = None
    refresh_interval: int = 30
    web_port: int = 8765
    default_action: str = "index_only"
    default_teleport_policy: str = "ask"
    follow_symlinks: bool = False
    folders: list[ManagedFolder] = Field(default_factory=list)
    files: list[ManagedFile] = Field(default_factory=list)


def default_config_path() -> Path:
    """Return the agent's config path (``$PARACORD_AGENT_HOME`` or ~/.config/paracord)."""
    env = os.environ.get("PARACORD_AGENT_HOME")
    base = Path(env).expanduser() if env else Path("~/.config/paracord").expanduser()
    return base / "agent.yaml"


def load_config(path: Path | None = None) -> AgentConfig:
    """Load the agent config (returns defaults if the file does not exist yet)."""
    resolved = Path(path).expanduser() if path else default_config_path()
    if not resolved.exists():
        return AgentConfig()
    data = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
    return AgentConfig(**data)


def save_config(config: AgentConfig, path: Path | None = None) -> Path:
    """Persist the agent config, creating the directory if needed."""
    resolved = Path(path).expanduser() if path else default_config_path()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(
        yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False), encoding="utf-8"
    )
    return resolved
