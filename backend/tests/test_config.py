"""Backend settings loading tests."""

from pathlib import Path

import pytest

from app.core.config import get_settings

# Settings env vars cleared so these tests are hermetic regardless of the ambient
# environment (e.g. DATABASE_URL is set inside the api container).
_CONFIG_ENV_VARS = [
    "PAPERRACKS_ENV",
    "PAPERRACKS_BIND_HOST",
    "PAPERRACKS_BIND_PORT",
    "PAPERRACKS_LAN_MODE",
    "PAPERRACKS_PUBLIC_BASE_URL",
    "PAPERRACKS_SESSION_TTL_MINUTES",
    "PAPERRACKS_SERVER_CONFIG",
    "DATABASE_URL",
    "REDIS_URL",
    "GROBID_URL",
    "OLLAMA_URL",
]


@pytest.fixture(autouse=True)
def clear_settings_cache(monkeypatch):
    """Isolate ambient settings env vars and keep cached settings from leaking."""
    for var in _CONFIG_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_settings_load_yaml_config(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "server.yaml"
    config_path.write_text(
        """
server:
  bind_host: 0.0.0.0
  bind_port: 9000
  allow_lan_access: true
  public_base_url: http://paperracks.local:9000
security:
  guest_access_enabled: false
services:
  database_url: postgresql+psycopg://example/example
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("PAPERRACKS_SERVER_CONFIG", str(config_path))
    monkeypatch.delenv("PAPERRACKS_BIND_PORT", raising=False)
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.bind_host == "0.0.0.0"
    assert settings.bind_port == 9000
    assert settings.lan_mode is True
    assert settings.public_base_url == "http://paperracks.local:9000"
    assert settings.guest_access_enabled is False
    assert settings.database_url == "postgresql+psycopg://example/example"


def test_environment_overrides_yaml_config(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "server.yaml"
    config_path.write_text("server:\n  bind_port: 9000\n", encoding="utf-8")
    monkeypatch.setenv("PAPERRACKS_SERVER_CONFIG", str(config_path))
    monkeypatch.setenv("PAPERRACKS_BIND_PORT", "9100")
    get_settings.cache_clear()

    assert get_settings().bind_port == 9100
