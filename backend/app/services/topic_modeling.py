"""Lightweight, dependency-free topic modeling (SPEC §8.15, default tier).

Rather than pulling in BERTopic / sentence-transformers (heavy, and a large
image), this clusters the works in a scope with TF-IDF + a small deterministic
k-means and labels each cluster by its top TF-IDF terms.

It is fully local, has no network egress, and is deliberately small enough for
fast development/test feedback. A real embedding/BERTopic backend can replace
``model_topics`` later behind the same interface; assignments are stamped with
``topic_model_id`` so results from different models never mix.
"""

import math
import re
import uuid
from collections import Counter

from sqlalchemy import delete, select, text
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
    backend: str = "tfidf",
    embedding_model: str | None = None,
    random_seed: int | None = None,
    allow_outliers: bool = False,
    hierarchical: bool = False,
    visible_ids: set[uuid.UUID] | None = None,
) -> dict:
    """Cluster a scope's works into keyword-labelled topics and persist assignments.

    The default ``tfidf`` backend is the dependency-free baseline. An ``embedding``/``bertopic``
    backend returns the same clusters enriched with BERTopic-style metadata (representative works,
    coherence, optional outliers + hierarchy). It is deterministic — the clustering is seeded from
    the stable title order — so a ``random_seed`` is accepted for API parity but not required.
    ``visible_ids`` (Phase H) restricts the scope to works the caller may see.
    """
    if backend in ("embedding", "bertopic"):
        return _model_topics_embedding(
            db,
            scope_type=scope_type,
            scope_id=scope_id,
            max_topics=max_topics,
            backend=backend,
            embedding_model=embedding_model,
            allow_outliers=allow_outliers,
            hierarchical=hierarchical,
            visible_ids=visible_ids,
        )

    model_id = f"keyword-kmeans:{scope_type}:{scope_id or 'all'}"
    works = _ordered_works(
        _scope_works(db, scope_type=scope_type, scope_id=scope_id, visible_ids=visible_ids)
    )
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


def extract_paper_topics(
    db: Session,
    *,
    work: Work,
    backend: str = "tfidf",
    embedding_model: str | None = None,
    max_topics: int = DEFAULT_MAX_TOPICS,
) -> list[str]:
    """Return up to ``max_topics`` representative topic terms for a single paper (Phase K).

    A corpus topic model (k-means over many docs) is meaningless for one document, so this is a
    deterministic single-doc term ranker: tokenize the paper's title + abstract + latest stored TEI
    body (reusing the module tokenizer + stopword set), rank terms by frequency (ties broken
    alphabetically for reproducibility) and return the top ``max_topics``. The admin-configured
    ``backend``/``embedding_model`` are accepted for provenance parity with ``model_topics`` (an
    embedding/BERTopic backend can replace the internals later behind this same signature), but the
    ranking is the same deterministic baseline regardless of backend. Returns ``[]`` for empty text.
    """
    _ = (backend, embedding_model)  # provenance only; deterministic ranking is backend-agnostic.
    tokens = _tokenize(_paper_text(db, work))
    if not tokens:
        return []
    counts = Counter(tokens)
    # Frequency desc, then term asc — deterministic across runs and platforms.
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [term for term, _ in ranked[: max(0, max_topics)]]


def _paper_text(db: Session, work: Work) -> str:
    """Title + abstract + latest stored TEI body for a single paper (Phase K topic source).

    Falls back to just title+abstract (via ``_doc_text``) when no TEI body is stored or it can't be
    parsed. The TEI lookup is best-effort: a DB/parse hiccup must not break topic extraction.
    """
    parts = [_doc_text(work)]
    try:
        from app.models.citation import RawTeiDocument
        from app.services.tei_parser import extract_body_text

        tei = db.scalar(
            select(RawTeiDocument)
            .where(RawTeiDocument.work_id == work.id)
            .order_by(RawTeiDocument.created_at.desc())
        )
        if tei is not None:
            body = extract_body_text(tei.tei_xml)
            if body:
                parts.append(body)
    except Exception:  # noqa: BLE001 - best effort; degrade to title+abstract.
        pass
    return " ".join(part for part in parts if part)


def _result(
    model_id: str,
    scope_type: str,
    scope_id: uuid.UUID | None,
    *,
    topics,
    work_count,
):
    return {
        "model_id": model_id,
        "scope_type": scope_type,
        "scope_id": str(scope_id) if scope_id else None,
        "work_count": work_count,
        "topics": topics,
    }


# Below this cosine-to-centroid a work is considered a topic outlier (when allow_outliers).
_OUTLIER_THRESHOLD = 0.05
REPRESENTATIVE_PER_TOPIC = 3


def _is_postgres(db: Session) -> bool:
    return db.bind is not None and db.bind.dialect.name == "postgresql"


def _l2(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec] if norm else vec


def _parse_pgvector(value) -> list[float]:
    """Parse pgvector's text form ('[a,b,c]') into a float list."""
    s = str(value).strip().strip("[]")
    return [float(x) for x in s.split(",")] if s else []


def _mean_pooled_by_column(
    db: Session, work_ids: list[uuid.UUID], column: str
) -> dict[uuid.UUID, list[float]]:
    """Mean-pool each work's stored chunk vectors under ``column`` (pgvector ``avg``), Postgres-only.

    Reuses vectors already computed at index time instead of re-embedding the whole scope — the
    difference between a topic-model button that returns in a moment vs. one that re-runs the model
    over every paper. ``column`` is a registry-provisioned name (^vec_[a-z0-9_]+$)."""
    if not work_ids or not re.match(r"^vec_[a-z0-9_]+$", column):
        return {}
    rows = db.execute(
        text(
            f"SELECT work_id, avg({column})::text FROM work_chunks "  # noqa: S608
            f"WHERE {column} IS NOT NULL AND work_id = ANY(:ids) GROUP BY work_id"
        ),
        {"ids": [str(w) for w in work_ids]},
    ).all()
    pooled: dict[uuid.UUID, list[float]] = {}
    for wid, v in rows:
        try:
            pooled[uuid.UUID(str(wid))] = _parse_pgvector(v)
        except (ValueError, TypeError):
            # Malformed pooled vector → skip; the caller falls back to embedding the doc text
            # rather than aborting the whole topic-model / graph request (audit: stability #5).
            continue
    return pooled


def _paper_dense_vectors(
    db: Session, works: list[Work], embedding_model: str | None
) -> tuple[list[dict[int, float]] | None, str | None]:
    """Dense per-paper vectors for embedding-based clustering (#21 / B1), as index-keyed dicts.

    Selects the model set (a specific registered model, ``multimode`` across all active models, or
    the configured default). Each model contributes a mean-pooled stored chunk vector when it has a
    column on Postgres, else a freshly embedded document vector; per-model vectors are L2-normalized
    and concatenated. Returns ``(None, None)`` when only the hash-BOW baseline is available — the
    caller then falls back to TF-IDF clustering and reports it honestly."""
    from app.services.embedding_registry import active_models, column_for, provider_for
    from app.services.embeddings import (
        DEFAULT_EMBEDDING_MODEL,
        resolve_embedding_provider,
    )
    from app.services.hybrid_search import MULTIMODE

    # Resolve the model set → list of (model_name, provider).
    selected: list[tuple[str, object]] = []
    if embedding_model == MULTIMODE:
        selected = [(m.model_name, provider_for(db, m.model_name)) for m in active_models(db)]
    elif embedding_model:
        selected = [(embedding_model, provider_for(db, embedding_model))]
    else:
        provider = resolve_embedding_provider(db=db).provider
        if provider.model_name != DEFAULT_EMBEDDING_MODEL:
            selected = [(provider.model_name, provider)]
    if not selected:
        return None, None  # hash-BOW only → let the caller use the TF-IDF baseline

    work_ids = [w.id for w in works]
    per_work: dict[uuid.UUID, list[float]] = {w.id: [] for w in works}
    for model_name, provider in selected:
        col = column_for(db, model_name)
        pooled = (
            _mean_pooled_by_column(db, work_ids, col[0])
            if col is not None and _is_postgres(db)
            else {}
        )
        for work in works:
            vec = pooled.get(work.id)
            if not vec:  # no stored chunk vectors for this work/model → embed its text now
                vec = provider.embed(_doc_text(work))
            per_work[work.id].extend(_l2([float(x) for x in vec]))

    label = MULTIMODE if embedding_model == MULTIMODE else selected[0][0]
    vectors = [{i: x for i, x in enumerate(per_work[w.id])} for w in works]
    return vectors, label


def _model_topics_embedding(
    db: Session,
    *,
    scope_type: str,
    scope_id: uuid.UUID | None,
    max_topics: int,
    backend: str,
    embedding_model: str | None,
    allow_outliers: bool,
    hierarchical: bool,
    visible_ids: set[uuid.UUID] | None = None,
) -> dict:
    """Embedding backend (B1): cluster papers by dense embedding vectors, label with TF-IDF terms.

    Paper vectors are mean-pooled stored chunk vectors (or freshly embedded text) under the selected
    model / multimode; k-means runs on those dense vectors while cluster **keywords** still come from
    TF-IDF centroids (human-readable labels). With only the hash-BOW baseline available it falls back
    to TF-IDF clustering and reports ``used_embeddings=False`` so the result is honest, not silently
    lexical. ``bertopic`` reuses this path (real BERTopic is deferred).
    """
    model_id = f"{backend}:{scope_type}:{scope_id or 'all'}"
    works = _ordered_works(
        _scope_works(db, scope_type=scope_type, scope_id=scope_id, visible_ids=visible_ids)
    )
    documents = [(work, _tokenize(_doc_text(work))) for work in works]
    documents = [(work, tokens) for work, tokens in documents if tokens]

    db.execute(delete(TopicAssignment).where(TopicAssignment.topic_model_id == model_id))

    if not documents:
        db.flush()
        return _embedding_result(
            model_id, scope_type, scope_id, backend, embedding_model, topics=[], work_count=0
        )

    # Labels always come from TF-IDF (human-readable terms); clustering uses dense embedding
    # vectors when a real model is available, else falls back to the TF-IDF vectors themselves.
    tfidf_vectors = _tfidf([tokens for _, tokens in documents])
    dense_vectors, resolved_model = _paper_dense_vectors(
        db, [w for w, _ in documents], embedding_model
    )
    used_embeddings = dense_vectors is not None
    cluster_vectors = dense_vectors if used_embeddings else tfidf_vectors
    k = max(1, min(max_topics, len(documents)))
    assignments = _kmeans(cluster_vectors, k)

    topics: list[dict] = []
    outliers: list[str] = []
    centroids: dict[int, dict[str, float]] = {}

    for topic_id in range(k):
        members = [i for i, cluster in enumerate(assignments) if cluster == topic_id]
        if not members:
            continue
        cluster_centroid = _centroid([cluster_vectors[i] for i in members])
        label_centroid = _centroid([tfidf_vectors[i] for i in members])
        centroids[topic_id] = label_centroid  # hierarchy is over the label (term) space
        scored = sorted(
            members, key=lambda i: _cosine(cluster_vectors[i], cluster_centroid), reverse=True
        )
        coherence = sum(_cosine(cluster_vectors[i], cluster_centroid) for i in members) / len(
            members
        )
        topics.append(
            {
                "topic_id": topic_id,
                "keywords": _cluster_keywords(label_centroid),
                "work_count": len(members),
                "representative_work_ids": [
                    str(documents[i][0].id) for i in scored[:REPRESENTATIVE_PER_TOPIC]
                ],
                "coherence_score": round(max(0.0, min(1.0, coherence)), 4),
            }
        )
        for i in members:
            work, _ = documents[i]
            similarity = _cosine(cluster_vectors[i], cluster_centroid)
            if allow_outliers and similarity < _OUTLIER_THRESHOLD:
                outliers.append(str(work.id))
            db.add(
                TopicAssignment(
                    topic_model_id=model_id,
                    scope_type=scope_type,
                    scope_id=str(scope_id) if scope_id else None,
                    work_id=work.id,
                    topic_id=topic_id,
                    score=round(similarity, 4),
                )
            )

    db.flush()
    return _embedding_result(
        model_id,
        scope_type,
        scope_id,
        backend,
        resolved_model if used_embeddings else embedding_model,
        topics=topics,
        work_count=len(documents),
        outlier_work_ids=outliers,
        hierarchy=_topic_hierarchy(centroids) if hierarchical else None,
        used_embeddings=used_embeddings,
    )


def _topic_hierarchy(centroids: dict[int, dict[str, float]]) -> list[dict]:
    """A minimal agglomerative view: nearest-centroid topic pairs and their similarity."""
    ids = sorted(centroids)
    merges: list[dict] = []
    for pos, a in enumerate(ids):
        best, best_sim = None, -1.0
        for b in ids[pos + 1 :]:
            sim = _cosine(centroids[a], centroids[b])
            if sim > best_sim:
                best, best_sim = b, sim
        if best is not None:
            merges.append({"topic_a": a, "topic_b": best, "similarity": round(best_sim, 4)})
    return merges


def _embedding_result(
    model_id: str,
    scope_type: str,
    scope_id: uuid.UUID | None,
    backend: str,
    embedding_model: str | None,
    *,
    topics,
    work_count,
    outlier_work_ids: list[str] | None = None,
    hierarchy: list[dict] | None = None,
    used_embeddings: bool = False,
) -> dict:
    return {
        "model_id": model_id,
        "backend": backend,
        "embedding_model": embedding_model,
        "scope_type": scope_type,
        "scope_id": str(scope_id) if scope_id else None,
        "work_count": work_count,
        "topics": topics,
        "outlier_work_ids": outlier_work_ids or [],
        "hierarchy": hierarchy,
        # True when clustering used real dense embeddings; False = TF-IDF fallback (honest flag).
        "used_embeddings": used_embeddings,
    }


def _ordered_works(works: list[Work]) -> list[Work]:
    """Return a stable work order before vectorization and k-means seeding.

    SQL result order is not guaranteed unless explicitly requested. The current
    lightweight k-means uses deterministic seed documents selected from the input
    order, so sorting here keeps local and CI behavior consistent.
    """

    return sorted(
        works,
        key=lambda work: (
            (work.normalized_title or work.canonical_title or "").casefold(),
            (work.canonical_title or "").casefold(),
            str(work.id),
        ),
    )


def _scope_works(
    db: Session,
    *,
    scope_type: str,
    scope_id: uuid.UUID | None,
    visible_ids: set[uuid.UUID] | None = None,
) -> list[Work]:
    if scope_type == "library":
        works = list(db.scalars(select(Work)).all())
    elif scope_type == "shelf":
        if scope_id is None:
            raise ValueError("scope id is required for a shelf topic model")
        works = list(
            db.scalars(
                select(Work)
                .join(ShelfWork, ShelfWork.work_id == Work.id)
                .where(ShelfWork.shelf_id == scope_id)
            ).all()
        )
    elif scope_type == "rack":
        if scope_id is None:
            raise ValueError("scope id is required for a rack topic model")
        works = list(
            db.scalars(
                select(Work)
                .join(ShelfWork, ShelfWork.work_id == Work.id)
                .join(RackShelf, RackShelf.shelf_id == ShelfWork.shelf_id)
                .where(RackShelf.rack_id == scope_id)
                .distinct()
            ).all()
        )
    else:
        raise ValueError(f"Unsupported topic scope: {scope_type}")
    if visible_ids is not None:
        works = [w for w in works if w.id in visible_ids]
    return works


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

    # Deterministic seeding: spread the initial centroids across the stable,
    # title-ordered documents produced by _ordered_works.
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
