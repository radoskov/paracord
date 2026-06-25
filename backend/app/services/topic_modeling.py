"""Lightweight, dependency-free topic modeling (SPEC §8.15, default tier).

Rather than pulling in BERTopic / sentence-transformers (heavy, and a large image), this clusters
the works in a scope with TF-IDF + a small deterministic k-means and labels each cluster by its
top TF-IDF terms. It is fully local (no egress), deterministic for a given input order (so it is
testable), and good enough to suggest tags. A real embedding/BERTopic backend can replace
``model_topics`` later behind the same interface; assignments are stamped with a ``topic_model_id``
so results from different models never mix.
"""

import math
import re
import uuid
from collections import Counter

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.ai import TopicAssignment
from app.models.organization import RackShelf, ShelfWork
from app.models.work import Work

DEFAULT_MAX_TOPICS = 5
KEYWORDS_PER_TOPIC = 6
_KMEANS_ITERS = 15

_WORD = re.compile(r"[A-Za-z][A-Za-z'-]{2,}")
_STOPWORDS = frozenset(
    [
        "the",
        "and",
        "for",
        "are",
        "but",
        "not",
        "you",
        "all",
        "any",
        "can",
        "has",
        "have",
        "had",
        "her",
        "was",
        "one",
        "our",
        "out",
        "his",
        "she",
        "him",
        "each",
        "with",
        "from",
        "this",
        "that",
        "these",
        "those",
        "then",
        "than",
        "they",
        "them",
        "their",
        "there",
        "here",
        "into",
        "over",
        "under",
        "more",
        "most",
        "such",
        "some",
        "only",
        "other",
        "will",
        "would",
        "could",
        "should",
        "about",
        "above",
        "after",
        "again",
        "against",
        "because",
        "been",
        "being",
        "below",
        "between",
        "both",
        "does",
        "doing",
        "during",
        "further",
        "itself",
        "once",
        "very",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "whom",
        "why",
        "how",
        "also",
        "using",
        "used",
        "use",
        "based",
        "approach",
        "method",
        "results",
        "paper",
        "model",
        "models",
        "data",
        "show",
        "shows",
        "propose",
        "proposed",
    ]
)


def model_topics(
    db: Session,
    *,
    scope_type: str,
    scope_id: uuid.UUID | None = None,
    max_topics: int = DEFAULT_MAX_TOPICS,
) -> dict:
    """Cluster a scope's works into keyword-labelled topics and persist the assignments."""
    model_id = f"keyword-kmeans:{scope_type}:{scope_id or 'all'}"
    works = _scope_works(db, scope_type=scope_type, scope_id=scope_id)
    documents = [(work, _tokenize(_doc_text(work))) for work in works]
    documents = [(work, tokens) for work, tokens in documents if tokens]

    # Re-running replaces this model's prior assignments for the scope.
    db.execute(delete(TopicAssignment).where(TopicAssignment.topic_model_id == model_id))

    if not documents:
        db.flush()
        return _result(model_id, scope_type, scope_id, topics=[], work_count=0)

    vectors = _tfidf([tokens for _, tokens in documents])
    k = max(1, min(max_topics, len(documents)))
    assignments = _kmeans(vectors, k)

    topics: list[dict] = []
    for topic_id in range(k):
        members = [i for i, cluster in enumerate(assignments) if cluster == topic_id]
        if not members:
            continue
        member_vectors = [vectors[i] for i in members]
        centroid = _centroid(member_vectors)
        topics.append(
            {
                "topic_id": topic_id,
                "keywords": _cluster_keywords(centroid),
                "work_count": len(members),
            }
        )
        for i in members:
            work, _ = documents[i]
            db.add(
                TopicAssignment(
                    topic_model_id=model_id,
                    scope_type=scope_type,
                    scope_id=str(scope_id) if scope_id else None,
                    work_id=work.id,
                    topic_id=topic_id,
                    score=round(_cosine(vectors[i], centroid), 4),
                )
            )
    db.flush()
    return _result(model_id, scope_type, scope_id, topics=topics, work_count=len(documents))


def _result(model_id: str, scope_type: str, scope_id: uuid.UUID | None, *, topics, work_count):
    return {
        "model_id": model_id,
        "scope_type": scope_type,
        "scope_id": str(scope_id) if scope_id else None,
        "work_count": work_count,
        "topics": topics,
    }


def _scope_works(db: Session, *, scope_type: str, scope_id: uuid.UUID | None) -> list[Work]:
    if scope_type == "library":
        return list(db.scalars(select(Work)).all())
    if scope_type == "shelf":
        if scope_id is None:
            raise ValueError("scope id is required for a shelf topic model")
        return list(
            db.scalars(
                select(Work)
                .join(ShelfWork, ShelfWork.work_id == Work.id)
                .where(ShelfWork.shelf_id == scope_id)
            ).all()
        )
    if scope_type == "rack":
        if scope_id is None:
            raise ValueError("scope id is required for a rack topic model")
        return list(
            db.scalars(
                select(Work)
                .join(ShelfWork, ShelfWork.work_id == Work.id)
                .join(RackShelf, RackShelf.shelf_id == ShelfWork.shelf_id)
                .where(RackShelf.rack_id == scope_id)
                .distinct()
            ).all()
        )
    raise ValueError(f"Unsupported topic scope: {scope_type}")


def _doc_text(work: Work) -> str:
    return " ".join(part for part in (work.canonical_title, work.abstract) if part)


def _tokenize(text: str) -> list[str]:
    return [w for w in _WORD.findall((text or "").lower()) if w not in _STOPWORDS]


def _tfidf(token_lists: list[list[str]]) -> list[dict[str, float]]:
    n_docs = len(token_lists)
    doc_freq: Counter[str] = Counter()
    for tokens in token_lists:
        doc_freq.update(set(tokens))
    idf = {term: math.log((1 + n_docs) / (1 + df)) + 1.0 for term, df in doc_freq.items()}

    vectors: list[dict[str, float]] = []
    for tokens in token_lists:
        counts = Counter(tokens)
        total = sum(counts.values()) or 1
        vectors.append({term: (count / total) * idf[term] for term, count in counts.items()})
    return vectors


def _kmeans(vectors: list[dict[str, float]], k: int) -> list[int]:
    if k <= 1:
        return [0] * len(vectors)
    # Deterministic seeding: spread the initial centroids across the (input-ordered) documents.
    centroids = [dict(vectors[(i * len(vectors)) // k]) for i in range(k)]
    assignments = [0] * len(vectors)
    for _ in range(_KMEANS_ITERS):
        changed = False
        for index, vector in enumerate(vectors):
            best, best_score = 0, -1.0
            for cluster, centroid in enumerate(centroids):
                score = _cosine(vector, centroid)
                if score > best_score:
                    best, best_score = cluster, score
            if assignments[index] != best:
                changed = True
            assignments[index] = best
        for cluster in range(k):
            members = [vectors[i] for i, c in enumerate(assignments) if c == cluster]
            if members:
                centroids[cluster] = _centroid(members)
        if not changed:
            break
    return assignments


def _centroid(vectors: list[dict[str, float]]) -> dict[str, float]:
    total: dict[str, float] = {}
    for vector in vectors:
        for term, value in vector.items():
            total[term] = total.get(term, 0.0) + value
    count = len(vectors)
    return {term: value / count for term, value in total.items()}


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    smaller, larger = (a, b) if len(a) <= len(b) else (b, a)
    dot = sum(value * larger.get(term, 0.0) for term, value in smaller.items())
    if dot == 0.0:
        return 0.0
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    return dot / (norm_a * norm_b)


def _cluster_keywords(centroid: dict[str, float]) -> list[str]:
    ranked = sorted(centroid.items(), key=lambda item: item[1], reverse=True)
    return [term for term, _ in ranked[:KEYWORDS_PER_TOPIC]]
