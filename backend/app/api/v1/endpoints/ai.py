"""Local AI: summaries, embeddings, and topic modeling endpoints."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.config import get_settings
from app.core.security import Role
from app.db.session import get_db
from app.models.user import User
from app.services.summarization import summarize_scope
from app.services.topic_modeling import model_topics

router = APIRouter()
DB_DEP = Depends(get_db)
EDITOR_DEP = Depends(require_roles(Role.OWNER, Role.EDITOR))


class ScopeSummaryRequest(BaseModel):
    scope_type: Literal["library", "shelf", "rack"]
    scope_id: uuid.UUID | None = None
    summary_type: Literal["extractive"] = "extractive"
    max_sentences: int = 8


class ScopeSummaryResponse(BaseModel):
    entity_type: str
    entity_id: str
    summary_type: str
    text: str
    model_name: str | None = None
    prompt_version: str | None = None
    work_count: int


@router.post("/summaries", response_model=ScopeSummaryResponse, status_code=status.HTTP_201_CREATED)
def create_scope_summary(
    payload: ScopeSummaryRequest,
    db: Session = DB_DEP,
    _: User = EDITOR_DEP,
) -> ScopeSummaryResponse:
    """Generate (replacing prior) an extractive summary over a library/shelf/rack scope."""
    try:
        summary, work_count = summarize_scope(
            db,
            scope_type=payload.scope_type,
            scope_id=payload.scope_id,
            summary_type=payload.summary_type,
            max_sentences=max(3, min(payload.max_sentences, 20)),
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


@router.post("/topics", response_model=TopicModelResponse)
def create_topic_model(
    payload: TopicRequest,
    db: Session = DB_DEP,
    _: User = EDITOR_DEP,
) -> TopicModelResponse:
    """Run the topic model over a scope (TF-IDF baseline or embedding backend) + store assignments."""
    backend = payload.backend or get_settings().topic_backend
    try:
        result = model_topics(
            db,
            scope_type=payload.scope_type,
            scope_id=payload.scope_id,
            max_topics=max(1, min(payload.max_topics, 20)),
            backend=backend,
            embedding_model=payload.embedding_model,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    return TopicModelResponse(**result)
