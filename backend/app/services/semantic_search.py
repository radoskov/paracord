"""Semantic (vector) search over works (SPEC §8.15).

Works are embedded from their title + abstract and the query is embedded the same way; results
are ranked by cosine similarity. Embeddings are computed lazily and cached in the ``embeddings``
table, so the first search after new imports does the indexing and later searches only embed
works that are not indexed yet.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai import Embedding
from app.models.work import Work
from app.services.embeddings import DEFAULT_EMBEDDING_MODEL, cosine_similarity, embed_text


@dataclass
class SearchHit:
    work: Work
    score: float


def _work_text(work: Work) -> str:
    return " ".join(part for part in (work.canonical_title, work.abstract) if part).strip()


def ensure_work_embeddings(db: Session, *, model_name: str = DEFAULT_EMBEDDING_MODEL) -> int:
    """Embed and store any works missing an embedding for ``model_name``. Returns count added."""
    indexed = set(
        db.scalars(
            select(Embedding.entity_id).where(
                Embedding.entity_type == "work", Embedding.model_name == model_name
            )
        ).all()
    )
    added = 0
    for work in db.scalars(select(Work)).all():
        if work.id in indexed:
            continue
        text = _work_text(work)
        if not text:
            continue
        vector = embed_text(text)
        db.add(
            Embedding(
                entity_type="work",
                entity_id=work.id,
                model_name=model_name,
                dim=len(vector),
                vector=vector,
            )
        )
        added += 1
    if added:
        db.flush()
    return added


def semantic_search(
    db: Session,
    query: str,
    *,
    limit: int = 10,
    model_name: str = DEFAULT_EMBEDDING_MODEL,
) -> list[SearchHit]:
    """Return works ranked by cosine similarity to the query (most similar first)."""
    ensure_work_embeddings(db, model_name=model_name)
    if not (query or "").strip():
        return []

    query_vector = embed_text(query)
    rows = db.scalars(
        select(Embedding).where(Embedding.entity_type == "work", Embedding.model_name == model_name)
    ).all()

    scored: list[tuple[uuid.UUID, float]] = []
    for embedding in rows:
        score = cosine_similarity(query_vector, embedding.vector)
        if score > 0.0:
            scored.append((embedding.entity_id, score))
    scored.sort(key=lambda item: item[1], reverse=True)
    top = scored[:limit]

    works = {
        work.id: work
        for work in db.scalars(select(Work).where(Work.id.in_([wid for wid, _ in top]))).all()
    }
    return [SearchHit(work=works[wid], score=score) for wid, score in top if wid in works]
