"""Backend settings loading tests."""

from pathlib import Path

import pytest
from app.core.config import get_settings

# Settings env vars cleared so these tests are hermetic regardless of the ambient
# environment (e.g. DATABASE_URL is set inside the api container).
_CONFIG_ENV_VARS = [
    "PARACORD_ENV",
    "PARACORD_BIND_HOST",
    "PARACORD_BIND_PORT",
    "PARACORD_LAN_MODE",
    "PARACORD_PUBLIC_BASE_URL",
    "PARACORD_SESSION_TTL_MINUTES",
    "PARACORD_SERVER_CONFIG",
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
  public_base_url: http://paracord.local:9000
services:
  database_url: postgresql+psycopg://example/example
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("PARACORD_SERVER_CONFIG", str(config_path))
    monkeypatch.delenv("PARACORD_BIND_PORT", raising=False)
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.bind_host == "0.0.0.0"
    assert settings.bind_port == 9000
    assert settings.lan_mode is True
    assert settings.public_base_url == "http://paracord.local:9000"
    assert settings.database_url == "postgresql+psycopg://example/example"


def test_settings_load_grobid_options(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "server.yaml"
    config_path.write_text(
        """
processing:
  grobid:
    consolidate_header: false
    consolidate_citations: false
    include_raw_citations: true
    segment_sentences: false
    include_coordinates:
      - ref
      - s
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("PARACORD_SERVER_CONFIG", str(config_path))
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.grobid_consolidate_header is False
    assert settings.grobid_consolidate_citations is False
    assert settings.grobid_include_raw_citations is True
    assert settings.grobid_segment_sentences is False
    assert settings.grobid_coordinate_elements == ["ref", "s"]


def test_settings_load_ocr_options(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "server.yaml"
    config_path.write_text(
        """
processing:
  ocr:
    backend: none
    timeout_seconds: 120
    language: deu
    skip_if_text_layer_good: false
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("PARACORD_SERVER_CONFIG", str(config_path))
    get_settings.cache_clear()

    settings = get_settings()
    assert settings.ocr_backend == "none"
    assert settings.ocr_timeout_seconds == 120
    assert settings.ocr_language == "deu"
    assert settings.ocr_skip_if_text_layer_good is False


def test_settings_ocr_enable_fallback_backcompat(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "server.yaml"
    config_path.write_text("processing:\n  ocr:\n    enable_fallback: false\n", encoding="utf-8")
    monkeypatch.setenv("PARACORD_SERVER_CONFIG", str(config_path))
    get_settings.cache_clear()
    assert get_settings().ocr_backend == "none"


def test_settings_advanced_extraction_selects_full_ml(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "server.yaml"
    config_path.write_text(
        "processing:\n  advanced_extraction:\n    nougat_enabled: true\n", encoding="utf-8"
    )
    monkeypatch.setenv("PARACORD_SERVER_CONFIG", str(config_path))
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.ocr_backend == "full_ml"
    assert settings.extraction_backend == "nougat"


def test_settings_ocr_defaults(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "server.yaml"
    config_path.write_text("server:\n  bind_port: 9000\n", encoding="utf-8")
    monkeypatch.setenv("PARACORD_SERVER_CONFIG", str(config_path))
    get_settings.cache_clear()
    assert get_settings().ocr_backend == "ocrmypdf"


def test_environment_overrides_yaml_config(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "server.yaml"
    config_path.write_text("server:\n  bind_port: 9000\n", encoding="utf-8")
    monkeypatch.setenv("PARACORD_SERVER_CONFIG", str(config_path))
    monkeypatch.setenv("PARACORD_BIND_PORT", "9100")
    get_settings.cache_clear()

    assert get_settings().bind_port == 9100
