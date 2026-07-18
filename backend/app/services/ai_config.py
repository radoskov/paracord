"""Effective AI provider configuration (WORKPLAN_NEXT Stage 8).

Resolves the **effective** AI settings by overlaying the owner-editable ``ai_config`` DB row on the
static ``Settings`` defaults (DB wins; an absent/empty row reproduces the out-of-the-box
lexical-baseline behavior). The embedding/summary/topic services read their provider choice from
here so it can be changed at runtime from the Admin UI rather than from a config file.
"""

from __future__ import annotations

import ipaddress
import uuid
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.ai import AI_CONFIG_SINGLETON_ID, AIConfig
from app.utils.table_presence import table_present


def _ai_config_table_present(db: Session) -> bool:
    """Whether the ``ai_config`` table exists (narrow unit-test schemas omit it)."""
    return table_present(db, AIConfig.__tablename__)


# Fields an owner may set via the API, and the known/allowed values for the enum-like ones.
EDITABLE_FIELDS = (
    "embedding_provider",
    "embedding_model",
    "summary_provider",
    "summary_model",
    "topic_backend",
    "topic_embedding_model",
    "ocr_backend",
    "ocr_language",
    "ollama_url",
    "vram_budget_gb",
)
EMBEDDING_PROVIDERS = ("hash_bow", "sentence_transformers", "ollama")
SUMMARY_PROVIDERS = ("extractive", "local_llm")
TOPIC_BACKENDS = ("tfidf", "embedding", "bertopic")
# OCR / advanced-extraction backends (Phase B5): none disables OCR; ocrmypdf adds a searchable
# text layer before GROBID; pymupdf adds one via PyMuPDF + tesseract (no ocrmypdf/ghostscript
# dependency). GROBID stays the structured TEI extractor either way.
OCR_BACKENDS = ("none", "ocrmypdf", "pymupdf")


@dataclass
class EffectiveAIConfig:
    """Resolved AI provider/model settings in effect right now (DB overlay applied)."""

    embedding_provider: str
    embedding_model: str | None
    summary_provider: str
    summary_model: str
    topic_backend: str
    topic_embedding_model: str | None
    ocr_backend: str
    # OCR languages in tesseract syntax; supports multi like "eng+spa" (passed through verbatim).
    ocr_language: str
    ollama_url: str
    # Admin-set memory budget (GB) for the Ollama host; None → no mount VRAM warning.
    vram_budget_gb: float | None

    def as_dict(self) -> dict:
        return asdict(self)


def _defaults(settings: Settings) -> EffectiveAIConfig:
    return EffectiveAIConfig(
        embedding_provider=settings.embedding_provider,
        embedding_model=settings.embedding_model,
        summary_provider="local_llm" if settings.summary_llm_enabled else "extractive",
        summary_model=settings.summary_llm_model,
        topic_backend=settings.topic_backend,
        topic_embedding_model=None,
        ocr_backend=settings.ocr_backend,
        ocr_language=settings.ocr_language,
        ollama_url=settings.ollama_url,
        vram_budget_gb=None,
    )


def get_ai_config(db: Session, *, settings: Settings | None = None) -> EffectiveAIConfig:
    """Return the effective AI config (DB row overlaid on Settings defaults)."""
    settings = settings or get_settings()
    cfg = _defaults(settings)
    # Probe table presence rather than provoking-then-rolling-back an error (E6): a read helper
    # must never roll back the caller's transaction.
    if not _ai_config_table_present(db):
        return cfg
    row = db.get(AIConfig, AI_CONFIG_SINGLETON_ID)
    if row is None:
        return cfg
    for field in EDITABLE_FIELDS:
        value = getattr(row, field, None)
        if value:
            setattr(cfg, field, value)
    # Tolerate a legacy/removed ocr_backend value (e.g. the dropped "full_ml") stored in an older
    # row: degrade to the Settings default rather than surfacing an out-of-range enum to callers.
    if cfg.ocr_backend not in OCR_BACKENDS:
        cfg.ocr_backend = settings.ocr_backend
    return cfg


def update_ai_config(
    db: Session, *, changes: dict, actor_user_id: uuid.UUID | None = None
) -> tuple[EffectiveAIConfig, bool]:
    """Validate + persist owner changes. Returns (effective config, embedding_model_changed).

    ``embedding_model_changed`` lets the caller schedule a reindex (vectors are stored per
    provider+model, so the active model must be rebuilt when it changes).
    """
    _validate(changes, settings=get_settings())
    before = get_ai_config(db)
    row = db.get(AIConfig, AI_CONFIG_SINGLETON_ID)
    if row is None:
        row = AIConfig(id=AI_CONFIG_SINGLETON_ID)
        db.add(row)
    for field in EDITABLE_FIELDS:
        if field in changes:
            setattr(row, field, changes[field] or None)
    row.updated_by_user_id = actor_user_id
    db.flush()
    after = get_ai_config(db)
    changed = (before.embedding_provider, before.embedding_model) != (
        after.embedding_provider,
        after.embedding_model,
    )
    return after, changed


def _ollama_host_is_local(host: str) -> bool:
    """True for a loopback IP, ``localhost``, or a bare docker-service name (single DNS label).

    Mirrors find-on-web's egress classification (:mod:`app.services.web_find`): loopback and
    single-label service names resolve inside the compose network and are always safe; an FQDN or a
    LAN/public IP literal is "other" and needs the explicit opt-in.
    """
    host = (host or "").strip().lower().rstrip(".")
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        # Not an IP literal: a bare single-label hostname is a docker-service name (e.g. "ollama");
        # a dotted FQDN is external.
        return "." not in host and ":" not in host


def _validate_ollama_url(url: str | None, *, settings: Settings) -> None:
    """Reject an admin-set ``ollama_url`` that could point egress at an arbitrary host (D6, SSRF).

    An empty value clears the override (falls back to the loopback Settings default). A non-loopback,
    non-service host is refused unless ``allow_external_ollama`` is explicitly enabled.
    """
    url = (url or "").strip()
    if not url:
        return
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https"):
        raise ValueError("ollama_url must be an http(s) URL")
    host = parts.hostname
    if not host:
        raise ValueError("ollama_url must include a host")
    if _ollama_host_is_local(host):
        return
    if not settings.allow_external_ollama:
        raise ValueError(
            "ollama_url host must be loopback or a docker-service name; set "
            "ALLOW_EXTERNAL_OLLAMA=true to allow an external Ollama host"
        )


def _validate(changes: dict, *, settings: Settings) -> None:
    if (
        changes.get("embedding_provider")
        and changes["embedding_provider"] not in EMBEDDING_PROVIDERS
    ):
        raise ValueError(f"Unknown embedding_provider (allowed: {EMBEDDING_PROVIDERS})")
    if changes.get("summary_provider") and changes["summary_provider"] not in SUMMARY_PROVIDERS:
        raise ValueError(f"Unknown summary_provider (allowed: {SUMMARY_PROVIDERS})")
    if changes.get("topic_backend") and changes["topic_backend"] not in TOPIC_BACKENDS:
        raise ValueError(f"Unknown topic_backend (allowed: {TOPIC_BACKENDS})")
    if changes.get("ocr_backend") and changes["ocr_backend"] not in OCR_BACKENDS:
        raise ValueError(f"Unknown ocr_backend (allowed: {OCR_BACKENDS})")
    if "ollama_url" in changes:
        _validate_ollama_url(changes["ollama_url"], settings=settings)
    if changes.get("vram_budget_gb") is not None:
        try:
            budget = float(changes["vram_budget_gb"])
        except (TypeError, ValueError) as exc:
            raise ValueError("vram_budget_gb must be a number of gigabytes") from exc
        if budget < 0:
            raise ValueError("vram_budget_gb must be non-negative")
