"""Admin-managed AI provider configuration + model management (WORKPLAN_NEXT Stage 8B/8C/8F).

Lets an owner or admin choose the embedding/summary/topic providers and models, detect what can run, pull
or delete model weights, and reindex embeddings — all from the web UI rather than a config file.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.db.session import get_db
from app.models.user import User
from app.models.work import Work
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
from app.services.model_catalog import search_models
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
    """Partial-update payload for the AI provider/model configuration."""

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
    """A (provider, model) pair identifying a downloadable/managed model."""

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


@router.get("/ai/models/search")
def ai_models_search(
    q: str = "", db: Session = DB_DEP, _: User = ADMIN_DEP
) -> dict:
    """Find pullable models by name/keyword, popularity-ranked, each with an estimated VRAM need.

    Ollama has no search API or VRAM reporting, so results come from a curated catalog plus a
    best-effort live enrichment from ollama.com (falls back to the catalog on any failure). Models
    already pulled locally are flagged so the UI can hide/label their Pull button (#5)."""
    cfg = get_ai_config(db)
    local = [m["name"] for m in list_models(ollama_url=cfg.ollama_url) if m.get("name")]
    return {"models": search_models(q, local_names=local)}


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
        from sqlalchemy.exc import OperationalError

        from app.services.embeddings import normalize_ollama_model

        try:
            unregister_by_model_name(db, f"ollama:{normalize_ollama_model(payload.model)}")
        except OperationalError as exc:
            db.rollback()
            if "lock timeout" in str(exc).lower():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="reindex in progress — try again later",
                ) from exc
            raise
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


@router.post("/ai/lexical-rebuild", status_code=status.HTTP_202_ACCEPTED)
def rebuild_lexical_index_endpoint(db: Session = DB_DEP, owner: User = ADMIN_DEP) -> dict:
    """Manually rebuild the lexical (BM25F+) index. Enqueues a background rebuild when a worker queue
    is available; otherwise (SQLite dev / Redis down) rebuilds synchronously so the button still
    takes effect. The index also self-rebuilds when the corpus changes — this is the explicit knob."""
    from app.services.bm25_index import force_rebuild  # noqa: PLC0415
    from app.workers.queue import enqueue_bm25_rebuild  # noqa: PLC0415 (keep Redis import lazy)

    job_id = enqueue_bm25_rebuild()
    if job_id is None:
        force_rebuild(db)
    record_event(
        db, "ai.lexical_rebuild_requested", actor_user_id=owner.id, entity_type="ai_config"
    )
    db.commit()
    return {"status": "queued" if job_id else "rebuilt", "job_id": job_id}


@router.get("/ai/reindex/status")
def reindex_status_endpoint(db: Session = DB_DEP, _: User = ADMIN_DEP) -> dict:
    """Embedding-index coverage for the active model (indexed / total)."""
    return reindex_status(db, provider=get_embedding_provider(db=db))


def _batch_field_work_ids(db: Session, field: str, scope: str) -> list:
    """Work ids to (re)process for a batch keyword/topic run (issue 12).

    ``scope='all'`` returns every current (non-merged) paper; ``scope='missing'`` returns only those
    whose ``keywords``/``topics`` list is empty/NULL. Filtered in Python so the empty-JSONB-list check
    is portable across SQLite (tests) and Postgres (prod)."""
    rows = db.execute(
        select(Work.id, getattr(Work, field)).where(Work.merged_into_id.is_(None))
    ).all()
    if scope == "all":
        return [wid for wid, _ in rows]
    return [wid for wid, value in rows if not value]  # None or empty list == "missing"


class BatchExtractRequest(BaseModel):
    """Scope selector for a library-wide batch keyword/topic extraction run."""

    # 'missing' only touches papers lacking the field; 'all' re-extracts/replaces for every paper.
    scope: Literal["all", "missing"] = "missing"


@router.get("/ai/keyword-topic-status")
def keyword_topic_status_endpoint(db: Session = DB_DEP, _: User = ADMIN_DEP) -> dict:
    """Per-paper keyword/topic coverage (issue 12): total papers and how many lack each."""
    rows = db.execute(select(Work.keywords, Work.topics).where(Work.merged_into_id.is_(None))).all()
    total = len(rows)
    return {
        "total": total,
        "keywords_missing": sum(1 for kw, _ in rows if not kw),
        "topics_missing": sum(1 for _, tp in rows if not tp),
    }


def _run_batch(db: Session, owner: User, *, field: str, scope: str, enqueue, event: str) -> dict:
    """Enqueue ``enqueue`` for each eligible work id, audit the run, and report counts.

    Raises 503 if there were eligible ids but the queue accepted none of them.
    """
    ids = _batch_field_work_ids(db, field, scope)
    queued = sum(1 for wid in ids if enqueue(wid) is not None)
    if ids and queued == 0:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Background queue unavailable — no jobs could be enqueued",
        )
    record_event(
        db,
        event,
        actor_user_id=owner.id,
        entity_type="ai_config",
        details={"scope": scope, "eligible": len(ids), "queued": queued},
    )
    db.commit()
    return {"scope": scope, "eligible": len(ids), "queued": queued}


@router.post("/ai/keywords/batch", status_code=status.HTTP_202_ACCEPTED)
def batch_keywords_endpoint(
    payload: BatchExtractRequest, db: Session = DB_DEP, owner: User = ADMIN_DEP
) -> dict:
    """Queue per-paper keyword extraction across the library (issue 12): ``all`` or ``missing`` only."""
    from app.workers.queue import enqueue_keywords

    return _run_batch(
        db,
        owner,
        field="keywords",
        scope=payload.scope,
        enqueue=enqueue_keywords,
        event="ai.batch_keywords_requested",
    )


@router.post("/ai/topics/batch", status_code=status.HTTP_202_ACCEPTED)
def batch_topics_endpoint(
    payload: BatchExtractRequest, db: Session = DB_DEP, owner: User = ADMIN_DEP
) -> dict:
    """Queue per-paper topic-term extraction across the library (issue 12): ``all`` or ``missing``."""
    from app.workers.queue import enqueue_topics

    return _run_batch(
        db,
        owner,
        field="topics",
        scope=payload.scope,
        enqueue=enqueue_topics,
        event="ai.batch_topics_requested",
    )


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
        "lexical_index": lexical_cache_info(db),
        "ollama_reachable": providers["ollama_reachable"],
        "bertopic_installed": providers["bertopic_installed"],
        "sentence_transformers_installed": providers["sentence_transformers_installed"],
        "active": _active_capability_status(config_dict, providers),
    }
