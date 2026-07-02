"""Local AI: summaries, embeddings, and topic modeling endpoints."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_min_role
from app.core.security import Role
from app.db.session import get_db
from app.models.ai import TopicAssignment
from app.models.organization import Shelf, ShelfWork, Tag, TagLink
from app.models.user import User
from app.services import access
from app.services.access_settings import get_default_access_level
from app.services.summarization import summarize_scope
from app.services.topic_modeling import model_topics
from app.utils.normalization import normalize_title

router = APIRouter()
DB_DEP = Depends(get_db)
# AI scope reads (summaries/topics) need at least an editor; shelf creation from a topic needs a
# librarian. Ladder-based so admin/owner always pass.
EDITOR_DEP = Depends(require_min_role(Role.EDITOR))
LIBRARIAN_DEP = Depends(require_min_role(Role.LIBRARIAN))


class ScopeSummaryRequest(BaseModel):
    scope_type: Literal["library", "shelf", "rack"]
    scope_id: uuid.UUID | None = None
    # 'local_llm' uses the configured Ollama model over the scope's abstracts, degrading to the
    # extractive engine when the LLM is disabled/unreachable (#10).
    summary_type: Literal["extractive", "local_llm"] = "extractive"
    max_sentences: int = 8
    model_name: str | None = None


class ScopeSummaryResponse(BaseModel):
    entity_type: str
    entity_id: str
    summary_type: str
    text: str
    model_name: str | None = None
    prompt_version: str | None = None
    work_count: int
    # Provider provenance (#10): what was requested vs used, and why it fell back if it did.
    provider_requested: str | None = None
    provider_used: str | None = None
    fallback: bool = False
    fallback_reason: str | None = None


@router.post("/summaries", response_model=ScopeSummaryResponse, status_code=status.HTTP_201_CREATED)
def create_scope_summary(
    payload: ScopeSummaryRequest,
    db: Session = DB_DEP,
    actor: User = EDITOR_DEP,
) -> ScopeSummaryResponse:
    """Generate (replacing prior) an extractive summary over a library/shelf/rack scope.

    Access control: a shelf/rack scope requires SEE on that container, and only papers the caller
    may SEE feed the summary."""
    if not access.can_see_scope_container(
        db, actor, scope_type=payload.scope_type, scope_id=payload.scope_id
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scope not found")
    try:
        summary, work_count = summarize_scope(
            db,
            scope_type=payload.scope_type,
            scope_id=payload.scope_id,
            summary_type=payload.summary_type,
            max_sentences=max(3, min(payload.max_sentences, 20)),
            model_name=payload.model_name,
            visible_ids=access.visible_work_ids(db, actor),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    return ScopeSummaryResponse(
        entity_type=summary.entity_type,
        entity_id=str(summary.entity_id),
        summary_type=summary.summary_type,
        text=summary.text,
        model_name=summary.model_name,
        prompt_version=summary.prompt_version,
        work_count=work_count,
        provider_requested=getattr(summary, "provider_requested", None),
        provider_used=getattr(summary, "provider_used", None),
        fallback=getattr(summary, "fallback", False),
        fallback_reason=getattr(summary, "fallback_reason", None),
    )


class TopicRequest(BaseModel):
    scope_type: Literal["library", "shelf", "rack"]
    scope_id: uuid.UUID | None = None
    max_topics: int = 5
    # 'tfidf' (default baseline) or 'embedding'/'bertopic' (richer, deterministic). None → config.
    backend: Literal["tfidf", "embedding", "bertopic"] | None = None
    embedding_model: str | None = None


class TopicRead(BaseModel):
    topic_id: int
    keywords: list[str]
    work_count: int
    representative_work_ids: list[str] = []
    coherence_score: float | None = None


class TopicModelResponse(BaseModel):
    model_id: str
    backend: str | None = None
    embedding_model: str | None = None
    scope_type: str
    scope_id: str | None = None
    work_count: int
    topics: list[TopicRead]
    outlier_work_ids: list[str] = []
    hierarchy: list[dict] | None = None
    # True when the embedding backend clustered on real dense vectors; False = TF-IDF fallback
    # (hash-BOW baseline) so the UI can be honest about which backend actually ran (B1).
    used_embeddings: bool = False
    # Papers skipped because they lack pre-indexed chunk vectors for the model (D19); the read path
    # never embeds inline, so the UI shows a "N papers not indexed for this model — reindex" notice.
    unindexed_work_count: int = 0


@router.post("/topics", response_model=TopicModelResponse)
def create_topic_model(
    payload: TopicRequest,
    db: Session = DB_DEP,
    actor: User = EDITOR_DEP,
) -> TopicModelResponse:
    """Run the topic model over a scope (TF-IDF baseline or embedding backend) + store assignments.

    Access control: a shelf/rack scope requires SEE on that container, and only papers the caller
    may SEE are clustered."""
    from app.services.ai_config import get_ai_config

    if not access.can_see_scope_container(
        db, actor, scope_type=payload.scope_type, scope_id=payload.scope_id
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scope not found")
    cfg = get_ai_config(db)
    backend = payload.backend or cfg.topic_backend
    try:
        result = model_topics(
            db,
            scope_type=payload.scope_type,
            scope_id=payload.scope_id,
            max_topics=max(1, min(payload.max_topics, 20)),
            backend=backend,
            embedding_model=payload.embedding_model or cfg.topic_embedding_model,
            visible_ids=access.visible_work_ids(db, actor),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    return TopicModelResponse(**result)


class TopicActionRequest(BaseModel):
    topic_model_id: str
    topic_id: int
    name: str  # tag name / shelf name


def _topic_work_ids(
    db: Session, model_id: str, topic_id: int, *, visible: set[uuid.UUID] | None
) -> list[uuid.UUID]:
    ids = list(
        db.scalars(
            select(TopicAssignment.work_id).where(
                TopicAssignment.topic_model_id == model_id, TopicAssignment.topic_id == topic_id
            )
        ).all()
    )
    if visible is not None:
        ids = [i for i in ids if i in visible]
    if not ids:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No works for that topic")
    return ids


@router.post("/topics/accept-as-tag", status_code=status.HTTP_201_CREATED)
def accept_topic_as_tag(
    payload: TopicActionRequest, db: Session = DB_DEP, actor: User = EDITOR_DEP
) -> dict:
    """Create a tag from a topic and apply it to the topic's works (SPEC §8.15.3).

    Only papers the caller may SEE are tagged."""
    work_ids = _topic_work_ids(
        db, payload.topic_model_id, payload.topic_id, visible=access.visible_work_ids(db, actor)
    )
    normalized = normalize_title(payload.name)
    tag = db.scalar(select(Tag).where(Tag.normalized_name == normalized))
    if tag is None:
        tag = Tag(name=payload.name, normalized_name=normalized, description="From topic model")
        db.add(tag)
        db.flush()
    tagged = 0
    for work_id in work_ids:
        exists = db.get(TagLink, {"tag_id": tag.id, "entity_type": "work", "entity_id": work_id})
        if exists is None:
            db.add(
                TagLink(
                    tag_id=tag.id,
                    entity_type="work",
                    entity_id=work_id,
                    created_by_user_id=actor.id,
                )
            )
            tagged += 1
    db.commit()
    return {"tag_id": str(tag.id), "tagged": tagged}


@router.post("/topics/create-shelf", status_code=status.HTTP_201_CREATED)
def create_shelf_from_topic(
    payload: TopicActionRequest, db: Session = DB_DEP, actor: User = LIBRARIAN_DEP
) -> dict:
    """Create a shelf from a topic and add the topic's works to it (SPEC §8.15.3).

    Creating a shelf is a librarian+ action; only papers the caller may SEE are added. The new
    shelf takes the global default access level."""
    work_ids = _topic_work_ids(
        db, payload.topic_model_id, payload.topic_id, visible=access.visible_work_ids(db, actor)
    )
    shelf = Shelf(
        name=payload.name,
        access_level=get_default_access_level(db),
        created_by_user_id=actor.id,
    )
    db.add(shelf)
    db.flush()
    for position, work_id in enumerate(work_ids):
        db.add(
            ShelfWork(
                shelf_id=shelf.id, work_id=work_id, position=position, added_by_user_id=actor.id
            )
        )
    db.commit()
    return {"shelf_id": str(shelf.id), "added": len(work_ids)}
