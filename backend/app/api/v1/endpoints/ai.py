"""Local AI: summaries, embeddings, and topic modeling endpoints."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_min_role
from app.core.security import Role
from app.db.session import get_db
from app.models.ai import TopicAssignment
from app.models.organization import Shelf, ShelfWork, Tag, TagLink
from app.models.user import User
from app.models.work import Work
from app.services import access
from app.services.access_settings import get_default_access_level
from app.services.app_config import effective_ai_scope_job_threshold
from app.services.scope_resolution import count_scope_works
from app.services.summarization import latest_scope_summary, summarize_scope
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
    # extractive engine when the LLM is disabled/unreachable (#10). Left unset (None), the endpoint
    # resolves it from the admin AI config: the configured summary provider/model is used when set,
    # otherwise it falls back to the extractive engine (L4).
    summary_type: Literal["extractive", "local_llm"] | None = None
    max_sentences: int = 8
    model_name: str | None = None
    # UX batch 4: which per-paper summary feeds the collection synthesis, and whether to force
    # those per-paper summaries to be (re)generated. Maps the Insights dropdown:
    #   use/create short → short + reuse ; use/create detailed → detailed + reuse
    #   regen short → short + regenerate ; regen detailed → detailed + regenerate
    paper_detail: Literal["short", "detailed"] = "short"
    regenerate_papers: bool = False


class ScopeSummaryResponse(BaseModel):
    # Optional-with-defaults so the queued variant (S15: no summary yet, just a job id) validates;
    # the inline path always fills them.
    entity_type: str | None = None
    entity_id: str | None = None
    summary_type: str | None = None
    text: str | None = None
    model_name: str | None = None
    prompt_version: str | None = None
    work_count: int = 0
    # S15: the scope was too large to run inline — a background job was queued instead. Poll the
    # Jobs list for ``job_id``, then fetch GET /ai/summaries/latest.
    queued: bool = False
    job_id: str | None = None
    # Provider provenance (#10): what was requested vs used, and why it fell back if it did.
    provider_requested: str | None = None
    provider_used: str | None = None
    fallback: bool = False
    fallback_reason: str | None = None
    # UX batch 4: which scope this summarizes ("whole library" / shelf name / rack name) + how
    # (v2 map-reduce: per-paper digests → chunked synthesis).
    scope_label: str | None = None
    method: str | None = None


@router.post("/summaries", response_model=ScopeSummaryResponse, status_code=status.HTTP_201_CREATED)
def create_scope_summary(
    payload: ScopeSummaryRequest,
    response: Response,
    db: Session = DB_DEP,
    actor: User = EDITOR_DEP,
) -> ScopeSummaryResponse:
    """Generate (replacing prior) an extractive summary over a library/shelf/rack scope.

    Access control: a shelf/rack scope requires SEE on that container, and only papers the caller
    may SEE feed the summary. Scopes above the admin ``ai_scope_job_threshold`` run on the
    background worker instead (202 + job id; poll the Jobs list, then GET /ai/summaries/latest)
    so a library-sized summary can't pin an API worker for minutes (S15/S16).
    """
    if not access.can_see_scope_container(
        db, actor, scope_type=payload.scope_type, scope_id=payload.scope_id
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scope not found")
    # Honor the admin AI config when the caller didn't pin a type: use the configured model-based
    # provider if one is set, else the extractive engine (L4). Same resolution per-work summaries use.
    summary_type = payload.summary_type
    if summary_type is None:
        from app.services.ai_config import get_ai_config

        ai_cfg = get_ai_config(db)
        summary_type = "local_llm" if ai_cfg.summary_provider == "local_llm" else "extractive"
    # E3: a SQL predicate — the scope query filters in the database, no materialized id set.
    visible = access.visible_work_condition(db, actor)
    try:
        scope_size = count_scope_works(
            db, payload.scope_type, payload.scope_id, visible_ids=visible
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if scope_size > effective_ai_scope_job_threshold(db):
        from app.workers.queue import enqueue_scope_summary

        job_id = enqueue_scope_summary(
            payload.scope_type,
            payload.scope_id,
            summary_type=summary_type,
            max_sentences=max(3, min(payload.max_sentences, 20)),
            model_name=payload.model_name,
            actor_user_id=str(actor.id),
            paper_detail=payload.paper_detail,
            regenerate_papers=payload.regenerate_papers,
        )
        if job_id is not None:
            response.status_code = status.HTTP_202_ACCEPTED
            return ScopeSummaryResponse(queued=True, job_id=job_id, work_count=scope_size)
        # Queue unavailable — fall through and run inline rather than fail the request.
    try:
        summary, work_count = summarize_scope(
            db,
            scope_type=payload.scope_type,
            scope_id=payload.scope_id,
            summary_type=summary_type,
            max_sentences=max(3, min(payload.max_sentences, 20)),
            model_name=payload.model_name,
            created_by_user_id=actor.id,
            visible_ids=visible,
            paper_detail=payload.paper_detail,
            regenerate_papers=payload.regenerate_papers,
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
        scope_label=(summary.params or {}).get("scope_label"),
        method=(summary.params or {}).get("method"),
    )


class TopicRequest(BaseModel):
    scope_type: Literal["library", "shelf", "rack"]
    scope_id: uuid.UUID | None = None
    max_topics: int = 5
    # 'tfidf' (default baseline) or 'embedding'/'bertopic' (richer, deterministic). None → config.
    backend: Literal["tfidf", "embedding", "bertopic"] | None = None
    embedding_model: str | None = None


class TopicWorkRef(BaseModel):
    id: str
    title: str | None = None


class TopicRead(BaseModel):
    topic_id: int
    keywords: list[str]
    work_count: int
    representative_work_ids: list[str] = []
    coherence_score: float | None = None
    # UX batch 4: the papers behind this topic (best-fit first), with titles for direct display.
    work_ids: list[str] = []
    works: list[TopicWorkRef] = []


class TopicModelResponse(BaseModel):
    # Optional so the queued variant (S15) validates; the inline path always fills it.
    model_id: str | None = None
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
    # S15: queued-as-background-job variant (scope above the admin threshold).
    queued: bool = False
    job_id: str | None = None


@router.get("/summaries/latest", response_model=ScopeSummaryResponse)
def read_latest_scope_summary(
    scope_type: Literal["library", "shelf", "rack"],
    scope_id: uuid.UUID | None = None,
    db: Session = DB_DEP,
    actor: User = EDITOR_DEP,
) -> ScopeSummaryResponse:
    """Return the most recent stored summary for a scope (the S15 async-completion read path)."""
    if not access.can_see_scope_container(db, actor, scope_type=scope_type, scope_id=scope_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scope not found")
    summary = latest_scope_summary(db, scope_type=scope_type, scope_id=scope_id)
    if summary is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No summary for this scope yet"
        )
    return ScopeSummaryResponse(
        entity_type=summary.entity_type,
        entity_id=str(summary.entity_id),
        summary_type=summary.summary_type,
        text=summary.text,
        model_name=summary.model_name,
        prompt_version=summary.prompt_version,
        work_count=(summary.params or {}).get("work_count", 0),
        provider_requested=summary.provider_requested,
        provider_used=summary.provider_used,
        fallback=summary.fallback,
        scope_label=(summary.params or {}).get("scope_label"),
        method=(summary.params or {}).get("method"),
    )


@router.post("/topics", response_model=TopicModelResponse)
def create_topic_model(
    payload: TopicRequest,
    response: Response,
    db: Session = DB_DEP,
    actor: User = EDITOR_DEP,
) -> TopicModelResponse:
    """Run the topic model over a scope (TF-IDF baseline or embedding backend) + store assignments.

    Access control: a shelf/rack scope requires SEE on that container, and only papers the caller
    may SEE are clustered. Scopes above the admin ``ai_scope_job_threshold`` run on the background
    worker instead (202 + job id; assignments land in the topic graph when done) — S15/S16.
    """
    from app.services.ai_config import get_ai_config

    if not access.can_see_scope_container(
        db, actor, scope_type=payload.scope_type, scope_id=payload.scope_id
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scope not found")
    cfg = get_ai_config(db)
    backend = payload.backend or cfg.topic_backend
    visible = access.visible_work_condition(db, actor)  # E3: SQL predicate, not an id set
    try:
        scope_size = count_scope_works(
            db, payload.scope_type, payload.scope_id, visible_ids=visible
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if scope_size > effective_ai_scope_job_threshold(db):
        from app.workers.queue import enqueue_scope_topics

        job_id = enqueue_scope_topics(
            payload.scope_type,
            payload.scope_id,
            max_topics=max(1, min(payload.max_topics, 20)),
            backend=backend,
            embedding_model=payload.embedding_model or cfg.topic_embedding_model,
            actor_user_id=str(actor.id),
        )
        if job_id is not None:
            response.status_code = status.HTTP_202_ACCEPTED
            return TopicModelResponse(
                queued=True,
                job_id=job_id,
                scope_type=payload.scope_type,
                scope_id=str(payload.scope_id) if payload.scope_id else None,
                work_count=scope_size,
                topics=[],
            )
        # Queue unavailable — fall through and run inline rather than fail the request.
    try:
        result = model_topics(
            db,
            scope_type=payload.scope_type,
            scope_id=payload.scope_id,
            max_topics=max(1, min(payload.max_topics, 20)),
            backend=backend,
            embedding_model=payload.embedding_model or cfg.topic_embedding_model,
            visible_ids=visible,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    return _with_topic_titles(db, TopicModelResponse(**result))


def _with_topic_titles(db: Session, response: TopicModelResponse) -> TopicModelResponse:
    """Fill each topic's ``works`` (id+title, best-fit first) from its ``work_ids`` in one query."""
    all_ids: set[uuid.UUID] = set()
    for topic in response.topics:
        for wid in topic.work_ids:
            try:
                all_ids.add(uuid.UUID(wid))
            except ValueError:
                continue
    if not all_ids:
        return response
    titles = {
        str(wid): title
        for wid, title in db.execute(
            select(Work.id, Work.canonical_title).where(Work.id.in_(all_ids))
        ).all()
    }
    for topic in response.topics:
        topic.works = [TopicWorkRef(id=wid, title=titles.get(wid)) for wid in topic.work_ids]
    return response


@router.get("/topics/latest", response_model=TopicModelResponse)
def read_latest_topics(
    scope_type: Literal["library", "shelf", "rack"],
    scope_id: uuid.UUID | None = None,
    db: Session = DB_DEP,
    actor: User = EDITOR_DEP,
) -> TopicModelResponse:
    """Reconstruct the stored topic model for a scope from its assignments (UX batch 4).

    This is the async-completion read path (mirror of GET /ai/summaries/latest): a background
    `topic_model_job` stores `TopicAssignment` rows but the response (keywords etc.) was lost —
    here the topics are rebuilt from the assignments (members ordered by score) and the keyword
    labels recomputed deterministically over each topic's member documents.
    """
    from app.services.ai_config import get_ai_config
    from app.services.topic_modeling import (
        _centroid,
        _cluster_keywords,
        _doc_text,
        _tfidf,
        _tokenize,
    )

    if not access.can_see_scope_container(db, actor, scope_type=scope_type, scope_id=scope_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scope not found")
    cfg = get_ai_config(db)
    suffix = f"{scope_type}:{scope_id or 'all'}"
    # Deterministic model ids: prefer the configured backend's model, else whichever exists.
    candidates = [f"keyword-kmeans:{suffix}", f"embedding:{suffix}", f"bertopic:{suffix}"]
    if cfg.topic_backend != "tfidf":
        candidates.insert(0, f"{cfg.topic_backend}:{suffix}")
    model_id = next(
        (
            mid
            for mid in dict.fromkeys(candidates)
            if db.scalar(
                select(TopicAssignment.id).where(TopicAssignment.topic_model_id == mid).limit(1)
            )
        ),
        None,
    )
    if model_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No topic model for this scope yet"
        )
    rows = db.execute(
        select(TopicAssignment.topic_id, TopicAssignment.work_id, TopicAssignment.score).where(
            TopicAssignment.topic_model_id == model_id
        )
    ).all()
    visible = access.visible_work_ids(db, actor)
    by_topic: dict[int, list[tuple[uuid.UUID, float]]] = {}
    for topic_id, work_id, score in rows:
        if visible is not None and work_id not in visible:
            continue
        by_topic.setdefault(topic_id, []).append((work_id, score or 0.0))
    works_by_id = {
        w.id: w
        for w in db.scalars(
            select(Work).where(Work.id.in_({wid for m in by_topic.values() for wid, _ in m}))
        ).all()
    }
    topics: list[dict] = []
    for topic_id in sorted(by_topic):
        members = sorted(by_topic[topic_id], key=lambda m: -m[1])
        member_works = [works_by_id[wid] for wid, _ in members if wid in works_by_id]
        if not member_works:
            continue
        docs = [t for t in (_tokenize(_doc_text(w)) for w in member_works) if t]
        keywords = _cluster_keywords(_centroid(_tfidf(docs))) if docs else []
        topics.append(
            {
                "topic_id": topic_id,
                "keywords": keywords,
                "work_count": len(member_works),
                "work_ids": [str(w.id) for w in member_works],
            }
        )
    response = TopicModelResponse(
        model_id=model_id,
        backend=model_id.split(":", 1)[0],
        scope_type=scope_type,
        scope_id=str(scope_id) if scope_id else None,
        work_count=sum(t["work_count"] for t in topics),
        topics=[TopicRead(**t) for t in topics],
    )
    return _with_topic_titles(db, response)


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
