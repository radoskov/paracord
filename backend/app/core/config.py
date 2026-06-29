"""Application settings.

Settings are loaded from conservative built-in defaults, optionally overlaid with the server YAML
file selected by ``PARACORD_SERVER_CONFIG``, and finally overridden by environment variables.
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_CONFIG_PATH = Path("config/server.local.yaml")


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load YAML settings from ``path`` if it exists."""
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Server config must be a YAML mapping: {path}")
    return data


def _server_settings_from_yaml(data: dict[str, Any]) -> dict[str, Any]:
    """Flatten supported YAML settings into the runtime settings shape."""
    server = data.get("server") or {}
    security = data.get("security") or {}
    services = data.get("services") or {}

    values: dict[str, Any] = {}
    if "bind_host" in server:
        values["bind_host"] = server["bind_host"]
    if "bind_port" in server:
        values["bind_port"] = server["bind_port"]
    if "allow_lan_access" in server:
        values["lan_mode"] = server["allow_lan_access"]
    if "public_base_url" in server:
        values["public_base_url"] = server["public_base_url"]
    if "guest_access_enabled" in security:
        values["guest_access_enabled"] = security["guest_access_enabled"]
    if "allowed_roles" in security:
        values["allowed_roles"] = security["allowed_roles"]
    if "session_ttl_minutes" in security:
        values["session_ttl_minutes"] = security["session_ttl_minutes"]
    if "database_url" in services:
        values["database_url"] = services["database_url"]
    if "redis_url" in services:
        values["redis_url"] = services["redis_url"]
    if "grobid_url" in services:
        values["grobid_url"] = services["grobid_url"]
    if "ollama_url" in services:
        values["ollama_url"] = services["ollama_url"]
    grobid = (data.get("processing") or {}).get("grobid") or {}
    if "consolidate_header" in grobid:
        values["grobid_consolidate_header"] = grobid["consolidate_header"]
    if "consolidate_citations" in grobid:
        values["grobid_consolidate_citations"] = grobid["consolidate_citations"]
    if "include_raw_citations" in grobid:
        values["grobid_include_raw_citations"] = grobid["include_raw_citations"]
    if "segment_sentences" in grobid:
        values["grobid_segment_sentences"] = grobid["segment_sentences"]
    if "include_coordinates" in grobid:
        values["grobid_coordinate_elements"] = grobid["include_coordinates"]
    storage = data.get("storage") or {}
    if "managed_library_root" in storage:
        values["managed_library_root"] = storage["managed_library_root"]
    if "server_allowed_roots" in storage:
        values["server_allowed_roots"] = storage["server_allowed_roots"]
    enrichment = data.get("metadata_enrichment") or {}
    if "enabled" in enrichment:
        values["enrichment_enabled"] = enrichment["enabled"]
    sources = enrichment.get("sources") or {}
    if "arxiv" in sources:
        values["enrichment_arxiv"] = sources["arxiv"]
    if "crossref" in sources:
        values["enrichment_crossref"] = sources["crossref"]
    if "openalex" in sources:
        values["enrichment_openalex"] = sources["openalex"]
    if "semantic_scholar" in sources:
        values["enrichment_semantic_scholar"] = sources["semantic_scholar"]
    if "crossref_mailto" in enrichment:
        values["crossref_mailto"] = enrichment["crossref_mailto"]
    return values


class Settings(BaseSettings):
    """Runtime settings for the PaRacORD backend."""

    model_config = SettingsConfigDict(populate_by_name=True)

    environment: str = Field(default="production", alias="PARACORD_ENV")
    bind_host: str = Field(default="127.0.0.1", alias="PARACORD_BIND_HOST")
    bind_port: int = Field(default=8000, alias="PARACORD_BIND_PORT")
    lan_mode: bool = Field(default=False, alias="PARACORD_LAN_MODE")
    public_base_url: str = Field(
        default="http://127.0.0.1:8000",
        alias="PARACORD_PUBLIC_BASE_URL",
    )
    database_url: str = Field(
        default="postgresql+psycopg://paperracks:paperracks_dev_password@localhost:5432/paperracks",
        alias="DATABASE_URL",
    )
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    grobid_url: str = Field(default="http://localhost:8070", alias="GROBID_URL")
    # GROBID extraction options (driven from the `grobid:` YAML block / env).
    grobid_consolidate_header: bool = True
    grobid_consolidate_citations: bool = True
    grobid_include_raw_citations: bool = True
    grobid_segment_sentences: bool = True
    # TEI elements to request PDF coordinates for (enables reader anchors); empty disables.
    grobid_coordinate_elements: list[str] = ["ref", "biblStruct", "s", "p"]
    ollama_url: str = Field(default="http://localhost:11434", alias="OLLAMA_URL")
    cors_origins: list[str] = ["http://127.0.0.1:5173", "http://localhost:5173"]
    guest_access_enabled: bool = False
    allowed_roles: list[str] = ["owner", "editor", "reader"]
    session_ttl_minutes: int = Field(default=720, alias="PARACORD_SESSION_TTL_MINUTES")
    managed_library_root: str = "./storage/library"
    server_allowed_roots: list[Any] = []
    enrichment_enabled: bool = True
    enrichment_arxiv: bool = True
    enrichment_crossref: bool = True
    enrichment_openalex: bool = False
    enrichment_semantic_scholar: bool = False
    crossref_mailto: str | None = None


def _environment_overrides() -> dict[str, Any]:
    """Return settings values explicitly provided through environment variables."""
    values: dict[str, Any] = {}
    env_settings = Settings()  # type: ignore[call-arg]
    for name, field in Settings.model_fields.items():
        alias = str(field.validation_alias or "")
        if alias and alias in os.environ:
            values[name] = getattr(env_settings, name)
    return values


@lru_cache
def get_settings() -> Settings:
    """Return cached settings."""
    config_path = Path(os.environ.get("PARACORD_SERVER_CONFIG", DEFAULT_CONFIG_PATH))
    yaml_values = _server_settings_from_yaml(_load_yaml(config_path))
    return Settings(**(yaml_values | _environment_overrides()))  # type: ignore[call-arg]
