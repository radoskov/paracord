"""Admin-managed AI provider configuration + model management (WORKPLAN_NEXT Stage 8B/8C/8F).

Lets an owner or admin choose the embedding/summary/topic providers and models, detect what can run, pull
or delete model weights, and reindex embeddings — all from the web UI rather than a config file.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.db.session import get_db
from app.models.user import User
from app.services.ai_config import (
    EMBEDDING_PROVIDERS,
    OCR_BACKENDS,
    SUMMARY_PROVIDERS,
    TOPIC_BACKENDS,
    get_ai_config,
    update_ai_config,
)
from app.services.audit import record_event
from app.services.bm25_index import cache_info as lexical_cache_info
from app.services.chunk_embeddings import chunk_embedding_status
from app.services.embedding_registry import unregister_by_model_name
from app.services.embeddings import get_embedding_provider
from app.services.model_management import (
    delete_model,
    detect_providers,
    list_models,
    probe_embedding_model,
)
from app.services.semantic_search import reindex_status
from app.workers.queue import enqueue_model_pull, enqueue_reindex

router = APIRouter()
DB_DEP = Depends(get_db)
ADMIN_DEP = Depends(require_admin)


class AIConfigUpdate(BaseModel):
    embedding_provider: str | None = None
    embedding_model: str | None = None
    summary_provider: str | None = None
    summary_model: str | None = None
    topic_backend: str | None = None
    topic_embedding_model: str | None = None
    ocr_backend: str | None = None
    ocr_language: str | None = None
    ollama_url: str | None = None


class ModelRef(BaseModel):
    provider: str
    model: str


@router.get("/ai-config")
def read_ai_config(db: Session = DB_DEP, _: User = ADMIN_DEP) -> dict:
    """Return the effective AI config + the allowed values for each provider field."""
    return {
        "config": get_ai_config(db).as_dict(),
        "allowed": {
            "embedding_provider": list(EMBEDDING_PROVIDERS),
            "summary_provider": list(SUMMARY_PROVIDERS),
            "topic_backend": list(TOPIC_BACKENDS),
            "ocr_backend": list(OCR_BACKENDS),
        },
    }


@router.put("/ai-config")
def write_ai_config(payload: AIConfigUpdate, db: Session = DB_DEP, owner: User = ADMIN_DEP) -> dict:
    """Update AI provider config (owner). Changing the embedding model queues a reindex."""
    changes = payload.model_dump(exclude_unset=True)
    try:
        config, embedding_changed = update_ai_config(db, changes=changes, actor_user_id=owner.id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    record_event(
        db, "ai.config_changed", actor_user_id=owner.id, entity_type="ai_config", details=changes
    )
    db.commit()
    reindex_job_id = enqueue_reindex() if embedding_changed else None
    return {"config": config.as_dict(), "reindex_job_id": reindex_job_id}


@router.get("/ai/providers")
def ai_providers(db: Session = DB_DEP, _: User = ADMIN_DEP) -> dict:
    """Detect which providers can run here (and how to enable those that can't)."""
    return detect_providers(ollama_url=get_ai_config(db).ollama_url)


@router.get("/ai/models")
def ai_models(db: Session = DB_DEP, _: User = ADMIN_DEP) -> dict:
    """List locally-available downloadable models."""
    return {"models": list_models(ollama_url=get_ai_config(db).ollama_url)}


@router.get("/ai/embedding-models")
def ai_embedding_models(db: Session = DB_DEP, _: User = ADMIN_DEP) -> dict:
    """Registered embedding models (each with its own chunk-vector column) for the search selector.

    The UI offers each of these plus a 'multimode' option (RRF across all) for semantic search and
    clustering (#21)."""
    from app.services.embedding_registry import MAX_EMBEDDING_MODELS, active_models

    providers = detect_providers(ollama_url=get_ai_config(db).ollama_url)

    def _available(provider: str) -> bool:
        # A registered model is usable only if its provider can run here (e.g. a seeded
        # sentence-transformers model is listed but unusable until that package is installed).
        return bool((providers.get("embedding") or {}).get(provider, {}).get("available", False))

    models = active_models(db)
    usable = [m for m in models if _available(m.provider)]
    return {
        "models": [
            {
                "model_name": m.model_name,
                "provider": m.provider,
                "dim": m.dim,
                "slug": m.slug,
                "available": _available(m.provider),
            }
            for m in models
        ],
        "max_models": MAX_EMBEDDING_MODELS,
        # Multimode only makes sense across models whose providers are actually usable.
        "multimode_available": len(usable) > 1,
    }


@router.post("/ai/models/validate")
def validate_embedding_model(payload: ModelRef, db: Session = DB_DEP, _: User = ADMIN_DEP) -> dict:
    """Check that an Ollama model is pulled and embedding-capable before selecting it (#2).

    Lets the admin UI warn "not pulled" / "not an embedding model" up front instead of the job
    silently degrading to hash-BOW. Only Ollama is probed; other providers report present."""
    if payload.provider != "ollama":
        return {"present": True, "embeddings": True, "canonical": payload.model, "error": None}
    return probe_embedding_model(payload.model, ollama_url=get_ai_config(db).ollama_url)


@router.post("/ai/models/pull", status_code=status.HTTP_202_ACCEPTED)
def pull_model_endpoint(payload: ModelRef, db: Session = DB_DEP, owner: User = ADMIN_DEP) -> dict:
    """Queue a model download/pull (tracked as a background job)."""
    job_id = enqueue_model_pull(payload.provider, payload.model)
    if job_id is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Model-pull queue unavailable"
        )
    record_event(
        db,
        "ai.model_pull_requested",
        actor_user_id=owner.id,
        entity_type="ai_model",
        details={"provider": payload.provider, "model": payload.model},
    )
    db.commit()
    return {"job_id": job_id, "status": "queued"}


@router.delete("/ai/models")
def delete_model_endpoint(payload: ModelRef, db: Session = DB_DEP, owner: User = ADMIN_DEP) -> dict:
    """Remove a locally-pulled model (Ollama)."""
    try:
        result = delete_model(
            payload.provider, payload.model, ollama_url=get_ai_config(db).ollama_url
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - surface daemon errors as a 502
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    # Also drop the model's chunk-vector column + HNSW index so a cap slot is freed (#21).
    if payload.provider == "ollama":
        from app.services.embeddings import normalize_ollama_model

        unregister_by_model_name(db, f"ollama:{normalize_ollama_model(payload.model)}")
    record_event(
        db,
        "ai.model_deleted",
        actor_user_id=owner.id,
        entity_type="ai_model",
        details={"provider": payload.provider, "model": payload.model},
    )
    db.commit()
    return result


@router.post("/ai/reindex", status_code=status.HTTP_202_ACCEPTED)
def reindex_endpoint(db: Session = DB_DEP, owner: User = ADMIN_DEP) -> dict:
    """Queue a full embedding reindex for the active provider."""
    job_id = enqueue_reindex()
    if job_id is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Reindex queue unavailable"
        )
    record_event(db, "ai.reindex_requested", actor_user_id=owner.id, entity_type="ai_config")
    db.commit()
    return {"job_id": job_id, "status": "queued"}


@router.get("/ai/reindex/status")
def reindex_status_endpoint(db: Session = DB_DEP, _: User = ADMIN_DEP) -> dict:
    """Embedding-index coverage for the active model (indexed / total)."""
    return reindex_status(db, provider=get_embedding_provider(db=db))


def _active_capability_status(config: dict, providers: dict) -> dict:
    """For each capability, report the selected provider/backend and whether it is available now.

    An unavailable active selection (e.g. embedding=ollama while Ollama is down) still runs — the
    services degrade to their dependency-free baseline — so we surface that honestly rather than
    pretending it is off.
    """

    def _entry(group: str, key: str) -> dict:
        info = (providers.get(group) or {}).get(key) or {}
        return {
            "selected": key,
            "available": bool(info.get("available", False)),
            "note": info.get("note"),
        }

    return {
        "embedding": _entry("embedding", config["embedding_provider"]),
        "summary": _entry("summary", config["summary_provider"]),
        "topic": _entry("topic", config["topic_backend"]),
        "extraction": _entry("extraction", config["ocr_backend"]),
    }


@router.get("/ai/status")
def ai_status_endpoint(db: Session = DB_DEP, _: User = ADMIN_DEP) -> dict:
    """Everything the AI & Models tab needs in one call: config, provider availability, the
    embedding-index coverage, capability flags and which selection is active per capability."""
    config = get_ai_config(db)
    config_dict = config.as_dict()
    providers = detect_providers(ollama_url=config.ollama_url)
    return {
        "config": config_dict,
        "allowed": {
            "embedding_provider": list(EMBEDDING_PROVIDERS),
            "summary_provider": list(SUMMARY_PROVIDERS),
            "topic_backend": list(TOPIC_BACKENDS),
            "ocr_backend": list(OCR_BACKENDS),
        },
        "providers": providers,
        "reindex": reindex_status(db, provider=get_embedding_provider(db=db)),
        # Hybrid search (HS6): chunk-level ANN coverage for the active model + lexical index warmth.
        "chunk_embeddings": chunk_embedding_status(db, provider=get_embedding_provider(db=db)),
        "lexical_index": lexical_cache_info(),
        "ollama_reachable": providers["ollama_reachable"],
        "bertopic_installed": providers["bertopic_installed"],
        "sentence_transformers_installed": providers["sentence_transformers_installed"],
        "active": _active_capability_status(config_dict, providers),
    }
