"""Effective AI provider configuration (WORKPLAN_NEXT Stage 8).

Resolves the **effective** AI settings by overlaying the owner-editable ``ai_config`` DB row on the
static ``Settings`` defaults (DB wins; an absent/empty row reproduces the out-of-the-box
lexical-baseline behavior). The embedding/summary/topic services read their provider choice from
here so it can be changed at runtime from the Admin UI rather than from a config file.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.ai import AI_CONFIG_SINGLETON_ID, AIConfig

# Per-engine memo of whether the ``ai_config`` table exists (narrow unit-test schemas omit it).
_TABLE_PRESENT: dict[int, bool] = {}


def _ai_config_table_present(db: Session) -> bool:
    bind = db.get_bind()
    key = id(bind)
    if key not in _TABLE_PRESENT:
        # Inspect the session's own connection rather than the engine: inspecting the engine checks
        # out a fresh connection and (on SQLite/StaticPool) issues a ROLLBACK that would discard the
        # caller's uncommitted rows. Using the session connection keeps the caller's transaction
        # (and any pending flush) intact.
        _TABLE_PRESENT[key] = inspect(db.connection()).has_table(AIConfig.__tablename__)
    return _TABLE_PRESENT[key]


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
)
EMBEDDING_PROVIDERS = ("hash_bow", "sentence_transformers", "ollama")
SUMMARY_PROVIDERS = ("extractive", "local_llm")
TOPIC_BACKENDS = ("tfidf", "embedding", "bertopic")
# OCR / advanced-extraction backends (Phase B5): none disables OCR; ocrmypdf adds a searchable
# text layer before GROBID; pymupdf adds one via PyMuPDF + tesseract (no ocrmypdf/ghostscript
# dependency); full_ml routes to an opt-in ML extractor (activate-when-present).
OCR_BACKENDS = ("none", "ocrmypdf", "pymupdf", "full_ml")


@dataclass
class EffectiveAIConfig:
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
    return cfg


def update_ai_config(
    db: Session, *, changes: dict, actor_user_id: uuid.UUID | None = None
) -> tuple[EffectiveAIConfig, bool]:
    """Validate + persist owner changes. Returns (effective config, embedding_model_changed).

    ``embedding_model_changed`` lets the caller schedule a reindex (vectors are stored per
    provider+model, so the active model must be rebuilt when it changes).
    """
    _validate(changes)
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


def _validate(changes: dict) -> None:
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
