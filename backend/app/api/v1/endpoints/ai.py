"""Local AI: summaries, embeddings, and topic modeling endpoints."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.security import Role
from app.db.session import get_db
from app.models.user import User
from app.services.topic_modeling import model_topics

router = APIRouter()
DB_DEP = Depends(get_db)
EDITOR_DEP = Depends(require_roles(Role.OWNER, Role.EDITOR))


@router.post("/summaries")
def create_summary_job() -> dict[str, str]:
    """Queue a scope-level summary job (per-work summaries live under /works/{id}/summaries)."""
    return {"status": "todo"}


class TopicRequest(BaseModel):
    scope_type: Literal["library", "shelf", "rack"]
    scope_id: uuid.UUID | None = None
    max_topics: int = 5


class TopicRead(BaseModel):
    topic_id: int
    keywords: list[str]
    work_count: int


class TopicModelResponse(BaseModel):
    model_id: str
    scope_type: str
    scope_id: str | None = None
    work_count: int
    topics: list[TopicRead]


@router.post("/topics", response_model=TopicModelResponse)
def create_topic_model(
    payload: TopicRequest,
    db: Session = DB_DEP,
    _: User = EDITOR_DEP,
) -> TopicModelResponse:
    """Run the lightweight (no-LLM) topic model over a scope and store the assignments."""
    try:
        result = model_topics(
            db,
            scope_type=payload.scope_type,
            scope_id=payload.scope_id,
            max_topics=max(1, min(payload.max_topics, 20)),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    return TopicModelResponse(**result)
