"""Agent configuration."""

import os
from pathlib import Path

import yaml
from pydantic import BaseModel


class AgentConfig(BaseModel):
    """Local agent configuration.

    Folders to index live in ``allowed_roots`` — edit the config file (or pass roots on the
    command line) to add/remove them; that is the agent's "manage folders" surface.
    """

    name: str = "workstation-agent"
    server_url: str = "http://127.0.0.1:8000"
    allowed_roots: list[Path] = []
    follow_symlinks: bool = False
    teleport_enabled: bool = True
    poll_interval_seconds: int = 30
    token_file: Path | None = None


def load_agent_config(path: Path) -> AgentConfig:
    """Load an :class:`AgentConfig` from the YAML config file (see config/agent.example.yaml)."""
    data = yaml.safe_load(Path(path).expanduser().read_text(encoding="utf-8")) or {}
    agent = data.get("agent") or {}
    filesystem = data.get("filesystem") or {}
    teleport = data.get("teleport") or {}
    values: dict = {}
    if "name" in agent:
        values["name"] = agent["name"]
    if "server_url" in agent:
        values["server_url"] = agent["server_url"]
    if "poll_interval_seconds" in agent:
        values["poll_interval_seconds"] = agent["poll_interval_seconds"]
    if "token_file" in agent:
        values["token_file"] = Path(str(agent["token_file"])).expanduser()
    if "allowed_roots" in filesystem:
        values["allowed_roots"] = [Path(str(r)).expanduser() for r in filesystem["allowed_roots"]]
    if "follow_symlinks" in filesystem:
        values["follow_symlinks"] = filesystem["follow_symlinks"]
    if "enabled" in teleport:
        values["teleport_enabled"] = teleport["enabled"]
    return AgentConfig(**values)


def resolve_token(explicit: str | None, config: AgentConfig | None) -> str | None:
    """Resolve the agent bearer token from --token, then $PARACORD_AGENT_TOKEN, then token_file."""
    if explicit:
        return explicit
    env = os.environ.get("PARACORD_AGENT_TOKEN")
    if env:
        return env
    if config and config.token_file and config.token_file.expanduser().exists():
        return config.token_file.expanduser().read_text(encoding="utf-8").strip()
    return None
