"""Agent config-loading and token-resolution tests."""

from __future__ import annotations

from pathlib import Path

from paperracks_agent.config import AgentConfig, load_agent_config, resolve_token


def test_load_agent_config(tmp_path: Path) -> None:
    config_path = tmp_path / "agent.yaml"
    config_path.write_text(
        f"""
agent:
  name: ws
  server_url: http://example:8000
  poll_interval_seconds: 12
filesystem:
  allowed_roots:
    - {tmp_path}
  follow_symlinks: true
teleport:
  enabled: true
""",
        encoding="utf-8",
    )
    cfg = load_agent_config(config_path)
    assert cfg.name == "ws"
    assert cfg.server_url == "http://example:8000"
    assert cfg.poll_interval_seconds == 12
    assert cfg.allowed_roots == [tmp_path]
    assert cfg.follow_symlinks is True


def test_resolve_token_precedence(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("PARACORD_AGENT_TOKEN", raising=False)
    assert resolve_token("explicit", None) == "explicit"

    monkeypatch.setenv("PARACORD_AGENT_TOKEN", "from-env")
    assert resolve_token(None, None) == "from-env"

    monkeypatch.delenv("PARACORD_AGENT_TOKEN", raising=False)
    token_file = tmp_path / "tok"
    token_file.write_text("from-file\n", encoding="utf-8")
    assert resolve_token(None, AgentConfig(token_file=token_file)) == "from-file"
    assert resolve_token(None, AgentConfig()) is None
