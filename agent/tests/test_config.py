"""Agent config + secrets tests (§32 S3)."""

from __future__ import annotations

from pathlib import Path

from paperracks_agent import secrets as agent_secrets
from paperracks_agent.config import AgentConfig, ManagedFolder, load_config, save_config


def test_config_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "agent.yaml"
    config = AgentConfig(
        name="laptop",
        server_url="http://server:8000",
        agent_id="abc",
        refresh_interval=15,
        web_port=9000,
        default_action="index_and_extract",
        folders=[
            ManagedFolder(
                path=tmp_path / "papers",
                mode="monitored",
                action="teleport",
                teleport_policy="allow",
            )
        ],
    )
    save_config(config, path)
    loaded = load_config(path)
    assert loaded.name == "laptop"
    assert loaded.server_url == "http://server:8000"
    assert loaded.agent_id == "abc"
    assert loaded.refresh_interval == 15
    assert loaded.web_port == 9000
    assert loaded.default_action == "index_and_extract"
    assert len(loaded.folders) == 1
    assert loaded.folders[0].action == "teleport"
    assert loaded.folders[0].teleport_policy == "allow"


def test_load_config_missing_returns_defaults(tmp_path: Path) -> None:
    config = load_config(tmp_path / "nope.yaml")
    assert config.server_url == "http://127.0.0.1:8000"
    assert config.web_port == 8765
    assert config.folders == []


def test_secrets_file_fallback(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PARACORD_AGENT_HOME", str(tmp_path))
    monkeypatch.setattr(agent_secrets, "_keyring", lambda: None)  # force the file backend
    monkeypatch.delenv("PARACORD_AGENT_TOKEN", raising=False)

    assert agent_secrets.get_secret("agent_token") is None
    agent_secrets.set_secret("agent_token", "tok-123")
    assert agent_secrets.get_secret("agent_token") == "tok-123"
    # File is owner-only (0600).
    mode = (tmp_path / "secrets.json").stat().st_mode & 0o777
    assert mode == 0o600
    assert agent_secrets.resolve_token(None) == "tok-123"
    assert agent_secrets.resolve_token("explicit") == "explicit"
    agent_secrets.delete_secret("agent_token")
    assert agent_secrets.get_secret("agent_token") is None
