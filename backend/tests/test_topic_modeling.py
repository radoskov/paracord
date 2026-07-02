"""Lightweight topic-modeling tests (M7).

The current topic backend is intentionally small: local TF-IDF plus a
lightweight deterministic k-means. These tests therefore validate stable
semantic contracts rather than exact centroid membership. That keeps the tests
useful for the current backend and for a later embedding/BERTopic backend.
"""

from collections import Counter
from pathlib import Path

import pytest
from app.db.base import Base
from app.models.ai import TopicAssignment
from app.models.organization import RackShelf, Shelf, ShelfWork
from app.models.work import Work
from app.services.topic_modeling import model_topics
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

ML_WORKS = [
    (
        "Attention Is All You Need",
        "The transformer architecture uses self attention for sequences.",
    ),
    (
        "BERT",
        "Bidirectional transformer pretraining improves language understanding with attention.",
    ),
    (
        "Vision Transformers",
        "Applying transformer attention to image patches for classification.",
    ),
]

COOKING_WORKS = [
    (
        "Sourdough Bread",
        "A recipe for fermenting and baking sourdough bread in the oven.",
    ),
    (
        "Pizza Dough",
        "Knead the dough and bake pizza in a hot oven for a crispy crust.",
    ),
    (
        "Banana Bread",
        "Mix bananas into batter and bake banana bread in the oven.",
    ),
]

EXPECTED_TOPIC_LABELS = {
    **{title: "machine_learning" for title, _ in ML_WORKS},
    **{title: "cooking" for title, _ in COOKING_WORKS},
}

ML_TOPIC_TERMS = {
    "attention",
    "bert",
    "classification",
    "language",
    "transformer",
    "transformers",
}

COOKING_TOPIC_TERMS = {
    "bake",
    "baking",
    "bread",
    "dough",
    "oven",
    "pizza",
    "recipe",
}


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'topics.db'}")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            Work.__table__,
            Shelf.__table__,
            ShelfWork.__table__,
            RackShelf.__table__,
            TopicAssignment.__table__,
        ],
    )
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with session_local() as session:
        yield session


def _shelf_with(db, groups) -> Shelf:
    shelf = Shelf(name="Topical")
    db.add(shelf)
    db.flush()

    for title, abstract in groups:
        work = Work(canonical_title=title, normalized_title=title.lower(), abstract=abstract)
        db.add(work)
        db.flush()
        db.add(ShelfWork(shelf_id=shelf.id, work_id=work.id))

    db.commit()
    return shelf


def _topic_assignments_by_title(db, model_id: str) -> dict[str, dict]:
    """Return persisted topic assignments keyed by canonical work title."""

    rows = db.execute(
        select(
            Work.canonical_title,
            TopicAssignment.topic_id,
            TopicAssignment.score,
        )
        .join(TopicAssignment, TopicAssignment.work_id == Work.id)
        .where(TopicAssignment.topic_model_id == model_id)
    ).all()

    return {
        str(title): {
            "topic_id": int(topic_id),
            "score": score,
        }
        for title, topic_id, score in rows
    }


def _label_counts_by_topic(
    assignments: dict[str, dict],
    expected_labels: dict[str, str],
) -> dict[int, Counter[str]]:
    """Count expected semantic labels inside each discovered topic."""

    counts_by_topic: dict[int, Counter[str]] = {}

    for title, expected_label in expected_labels.items():
        assignment = assignments.get(title)
        if assignment is None:
            continue

        topic_id = assignment["topic_id"]
        counts_by_topic.setdefault(topic_id, Counter())[expected_label] += 1

    return counts_by_topic


def _dominant_topic_by_expected_label(
    assignments: dict[str, dict],
    expected_labels: dict[str, str],
) -> dict[str, tuple[int, int, Counter[int]]]:
    """Return each semantic label's dominant discovered topic.

    The tuple is:

        dominant_topic_id, dominant_count, all_topic_counts_for_label
    """

    result: dict[str, tuple[int, int, Counter[int]]] = {}

    for label in sorted(set(expected_labels.values())):
        topic_counts = Counter(
            assignments[title]["topic_id"]
            for title, expected_label in expected_labels.items()
            if expected_label == label and title in assignments
        )

        if not topic_counts:
            continue

        dominant_topic_id, dominant_count = topic_counts.most_common(1)[0]
        result[label] = (dominant_topic_id, dominant_count, topic_counts)

    return result


def _topic_keywords_by_id(result: dict) -> dict[int, set[str]]:
    return {int(topic["topic_id"]): set(topic["keywords"]) for topic in result["topics"]}


def _topic_debug(
    result: dict,
    assignments: dict[str, dict],
    expected_labels: dict[str, str],
) -> str:
    """Build a readable diagnostic report for topic-modeling assertion failures."""

    topics_by_id = {int(topic["topic_id"]): topic for topic in result.get("topics", [])}
    label_counts = _label_counts_by_topic(assignments, expected_labels)

    lines = [
        "Topic-modeling diagnostic report:",
        f"  model_id: {result.get('model_id')}",
        f"  scope_type: {result.get('scope_type')}",
        f"  scope_id: {result.get('scope_id')}",
        f"  reported_work_count: {result.get('work_count')}",
        "  topics:",
    ]

    for topic_id in sorted(topics_by_id):
        topic = topics_by_id[topic_id]
        lines.append(
            "    "
            f"topic={topic_id} "
            f"reported_work_count={topic.get('work_count')} "
            f"keywords={topic.get('keywords', [])} "
            f"expected_label_counts={dict(label_counts.get(topic_id, {}))}"
        )

    lines.append("  assignments:")

    for title in sorted(expected_labels):
        assignment = assignments.get(title)

        if assignment is None:
            lines.append(f"    {title!r}: MISSING expected_label={expected_labels[title]}")
            continue

        topic_id = assignment["topic_id"]
        topic = topics_by_id.get(topic_id, {})

        lines.append(
            "    "
            f"{title!r}: "
            f"expected_label={expected_labels[title]} "
            f"topic={topic_id} "
            f"score={assignment.get('score')} "
            f"topic_keywords={topic.get('keywords', [])}"
        )

    return "\n".join(lines)


def _assert_semantic_topic_separation(
    db,
    result: dict,
    expected_labels: dict[str, str],
) -> None:
    """Assert robust semantic separation without requiring exact cluster sizes."""

    assignments = _topic_assignments_by_title(db, result["model_id"])
    debug = _topic_debug(result, assignments, expected_labels)

    assert result["work_count"] == len(expected_labels), debug
    assert len(result["topics"]) == 2, debug
    assert set(assignments) == set(expected_labels), debug

    reported_counts = {
        int(topic["topic_id"]): int(topic["work_count"]) for topic in result["topics"]
    }
    persisted_counts = Counter(assignment["topic_id"] for assignment in assignments.values())

    assert dict(persisted_counts) == reported_counts, debug
    assert all(count > 0 for count in reported_counts.values()), debug

    label_counts_by_topic = _label_counts_by_topic(assignments, expected_labels)
    correctly_grouped = sum(
        max(label_counts.values()) for label_counts in label_counts_by_topic.values()
    )

    assert correctly_grouped >= 5, (
        "Expected the two-topic model to separate the clearly distinct ML and "
        "cooking groups with at most one cross-assigned work in this six-work "
        "toy corpus.\n"
        f"{debug}"
    )

    dominant = _dominant_topic_by_expected_label(assignments, expected_labels)

    assert set(dominant) == {"machine_learning", "cooking"}, debug

    ml_topic_id, ml_count, ml_topic_counts = dominant["machine_learning"]
    cooking_topic_id, cooking_count, cooking_topic_counts = dominant["cooking"]

    assert ml_count >= 2, (
        "Expected at least two of the three ML works to share a dominant topic. "
        f"Observed ML topic counts: {dict(ml_topic_counts)}\n"
        f"{debug}"
    )
    assert cooking_count >= 2, (
        "Expected at least two of the three cooking works to share a dominant topic. "
        f"Observed cooking topic counts: {dict(cooking_topic_counts)}\n"
        f"{debug}"
    )
    assert ml_topic_id != cooking_topic_id, (
        f"Expected ML and cooking works to have different dominant topics.\n{debug}"
    )

    keywords_by_topic = _topic_keywords_by_id(result)
    ml_keywords = keywords_by_topic[ml_topic_id]
    cooking_keywords = keywords_by_topic[cooking_topic_id]

    assert ML_TOPIC_TERMS & ml_keywords, (
        "Expected the dominant ML topic to expose at least one ML keyword. "
        f"Expected one of {sorted(ML_TOPIC_TERMS)}, got {sorted(ml_keywords)}.\n"
        f"{debug}"
    )
    assert COOKING_TOPIC_TERMS & cooking_keywords, (
        "Expected the dominant cooking topic to expose at least one cooking keyword. "
        f"Expected one of {sorted(COOKING_TOPIC_TERMS)}, got {sorted(cooking_keywords)}.\n"
        f"{debug}"
    )
    assert ml_keywords != cooking_keywords, debug


def _semantic_signature(db, result: dict, expected_labels: dict[str, str]) -> dict[str, int]:
    """Map each expected semantic label to its dominant topic id."""

    assignments = _topic_assignments_by_title(db, result["model_id"])
    dominant = _dominant_topic_by_expected_label(assignments, expected_labels)
    return {label: topic_id for label, (topic_id, _, _) in dominant.items()}


def test_topics_separate_distinct_groups(db_session) -> None:
    """Clearly different paper groups should produce distinct dominant topics.

    This test intentionally does not require an exact 3/3 cluster split. The
    current backend is TF-IDF + k-means, and a future backend may be
    embedding/BERTopic-based. Neither backend should be required to produce
    perfectly balanced clusters. The required behavior is semantic: every work
    is assigned once, two non-empty topics are produced, ML and cooking papers
    have different dominant topics, and the topic labels expose useful terms.
    """

    shelf = _shelf_with(db_session, ML_WORKS + COOKING_WORKS)
    result = model_topics(db_session, scope_type="shelf", scope_id=shelf.id, max_topics=2)
    db_session.commit()

    _assert_semantic_topic_separation(db_session, result, EXPECTED_TOPIC_LABELS)


def test_topics_are_stable_for_different_insertion_orders(db_session) -> None:
    """Topic semantics should not depend on DB insertion order.

    The lightweight backend seeds k-means from input-ordered documents. The
    service should therefore impose a deterministic work ordering before
    vectorization. This test protects against local/CI differences caused by
    database row order or fixture construction order.
    """

    natural_shelf = _shelf_with(db_session, ML_WORKS + COOKING_WORKS)
    mixed_shelf = _shelf_with(
        db_session,
        [
            COOKING_WORKS[2],
            ML_WORKS[1],
            COOKING_WORKS[0],
            ML_WORKS[2],
            COOKING_WORKS[1],
            ML_WORKS[0],
        ],
    )

    natural = model_topics(
        db_session,
        scope_type="shelf",
        scope_id=natural_shelf.id,
        max_topics=2,
    )
    mixed = model_topics(
        db_session,
        scope_type="shelf",
        scope_id=mixed_shelf.id,
        max_topics=2,
    )
    db_session.commit()

    _assert_semantic_topic_separation(db_session, natural, EXPECTED_TOPIC_LABELS)
    _assert_semantic_topic_separation(db_session, mixed, EXPECTED_TOPIC_LABELS)

    natural_labels = _semantic_signature(db_session, natural, EXPECTED_TOPIC_LABELS)
    mixed_labels = _semantic_signature(db_session, mixed, EXPECTED_TOPIC_LABELS)

    assert natural_labels.keys() == mixed_labels.keys()


def test_topics_persist_assignments_and_are_idempotent(db_session) -> None:
    shelf = _shelf_with(db_session, ML_WORKS + COOKING_WORKS)

    model_topics(db_session, scope_type="shelf", scope_id=shelf.id, max_topics=2)
    db_session.commit()

    first = db_session.scalar(select(func.count()).select_from(TopicAssignment))
    assert first == 6

    # Re-running replaces, not duplicates.
    model_topics(db_session, scope_type="shelf", scope_id=shelf.id, max_topics=2)
    db_session.commit()

    assert db_session.scalar(select(func.count()).select_from(TopicAssignment)) == 6
    rows = db_session.scalars(select(TopicAssignment)).all()
    assert all(r.topic_model_id == f"keyword-kmeans:shelf:{shelf.id}" for r in rows)


def test_topics_caps_k_at_document_count(db_session) -> None:
    shelf = _shelf_with(db_session, ML_WORKS[:2])  # only 2 docs

    result = model_topics(db_session, scope_type="shelf", scope_id=shelf.id, max_topics=5)
    db_session.commit()

    assert len(result["topics"]) <= 2


def test_topics_empty_scope_returns_empty(db_session) -> None:
    shelf = Shelf(name="Empty")
    db_session.add(shelf)
    db_session.commit()

    result = model_topics(db_session, scope_type="shelf", scope_id=shelf.id)
    db_session.commit()

    assert result["work_count"] == 0
    assert result["topics"] == []


def test_topics_shelf_requires_id(db_session) -> None:
    with pytest.raises(ValueError, match="scope id is required"):
        model_topics(db_session, scope_type="shelf", scope_id=None)


# --- API --------------------------------------------------------------------


def test_topics_api_runs_for_editor(client, auth_headers, db) -> None:
    shelf = Shelf(name="Scope")
    db.add(shelf)
    db.flush()

    for title, abstract in ML_WORKS:
        work = Work(canonical_title=title, normalized_title=title.lower(), abstract=abstract)
        db.add(work)
        db.flush()
        db.add(ShelfWork(shelf_id=shelf.id, work_id=work.id))

    db.commit()

    r = client.post(
        "/api/v1/ai/topics",
        headers=auth_headers("editor"),
        json={"scope_type": "shelf", "scope_id": str(shelf.id), "max_topics": 2},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["work_count"] == 3
    assert body["topics"]


def test_topics_api_requires_editor(client, auth_headers, db) -> None:
    shelf = Shelf(name="Scope")
    db.add(shelf)
    db.commit()

    r = client.post(
        "/api/v1/ai/topics",
        headers=auth_headers("reader"),
        json={"scope_type": "shelf", "scope_id": str(shelf.id)},
    )

    assert r.status_code == 403


# --- BERTopic-style backend --------------------------------------------------
# The ``bertopic`` backend reuses the deterministic TF-IDF + k-means clustering and layers a
# richer result shape (representative works, coherence, optional outliers + hierarchy) on top,
# echoing the requested embedding model for provenance. A real sentence-transformers/BERTopic
# implementation can drop in behind the same contract; these tests pin that contract.

RICH_TOPIC_CORPUS = [
    (
        "Transformer pruning for efficient inference",
        "Attention heads and transformer layers can be pruned to reduce latency.",
    ),
    (
        "Distilling language models for edge devices",
        "Knowledge distillation compresses large language models for deployment.",
    ),
    (
        "Quantization-aware training for neural networks",
        "Low-bit quantization improves model serving efficiency.",
    ),
    (
        "Sourdough fermentation microbiome",
        "Lactic acid bacteria and yeast shape sourdough flavor during fermentation.",
    ),
    (
        "Whole-grain baking texture analysis",
        "Hydration and oven temperature influence bread crumb and crust texture.",
    ),
    (
        "Pizza dough gluten development",
        "Kneading and fermentation determine gluten networks in pizza dough.",
    ),
]


def test_bertopic_backend_returns_provenance_and_representative_papers(db_session) -> None:
    """BERTopic-style results expose provenance and representative works."""

    shelf = _shelf_with(db_session, RICH_TOPIC_CORPUS)

    result = model_topics(
        db_session,
        scope_type="shelf",
        scope_id=shelf.id,
        max_topics=2,
        backend="bertopic",
        embedding_model="sentence-transformers/all-MiniLM-L6-v2",
    )

    assert result["backend"] == "bertopic"
    assert result["embedding_model"] == "sentence-transformers/all-MiniLM-L6-v2"
    assert result["model_id"].startswith("bertopic:shelf:")
    assert result["work_count"] == len(RICH_TOPIC_CORPUS)
    assert len(result["topics"]) == 2

    for topic in result["topics"]:
        assert topic["keywords"]
        assert topic["representative_work_ids"]
        assert topic["coherence_score"] is None or 0.0 <= topic["coherence_score"] <= 1.0
        assert topic["work_count"] >= 2


def test_bertopic_backend_is_stable_across_repeated_runs(db_session) -> None:
    """Seeded runs of a stochastic backend are deterministic."""

    shelf = _shelf_with(db_session, RICH_TOPIC_CORPUS)

    first = model_topics(
        db_session,
        scope_type="shelf",
        scope_id=shelf.id,
        max_topics=2,
        backend="bertopic",
        random_seed=42,
    )
    second = model_topics(
        db_session,
        scope_type="shelf",
        scope_id=shelf.id,
        max_topics=2,
        backend="bertopic",
        random_seed=42,
    )

    first_keywords = [set(topic["keywords"]) for topic in first["topics"]]
    second_keywords = [set(topic["keywords"]) for topic in second["topics"]]

    assert first_keywords == second_keywords


def test_bertopic_backend_supports_hierarchical_or_outlier_metadata(db_session) -> None:
    """BERTopic-style metadata is optional but structured when requested."""

    shelf = _shelf_with(db_session, RICH_TOPIC_CORPUS)

    result = model_topics(
        db_session,
        scope_type="shelf",
        scope_id=shelf.id,
        max_topics=4,
        backend="bertopic",
        allow_outliers=True,
        hierarchical=True,
    )

    assert "outlier_work_ids" in result
    assert "hierarchy" in result
    assert isinstance(result["outlier_work_ids"], list)
    assert result["hierarchy"] is None or isinstance(result["hierarchy"], list)


def test_bertopic_backend_degrades_gracefully_on_tiny_scopes(db_session) -> None:
    """Heavy backends fall back rather than error on a one-paper corpus."""

    shelf = _shelf_with(db_session, RICH_TOPIC_CORPUS[:1])

    result = model_topics(
        db_session,
        scope_type="shelf",
        scope_id=shelf.id,
        max_topics=10,
        backend="bertopic",
    )

    assert result["work_count"] == 1
    assert len(result["topics"]) == 1
    assert result["topics"][0]["work_count"] == 1
    assert result["topics"][0]["keywords"]


# --- B1: embedding backend clusters on dense vectors, labels from TF-IDF ---


def test_parse_pgvector_and_l2_helpers() -> None:
    from app.services.topic_modeling import _l2, _parse_pgvector

    assert _parse_pgvector("[1,2,3]") == [1.0, 2.0, 3.0]
    assert _parse_pgvector("[]") == []
    unit = _l2([3.0, 4.0])
    assert abs((unit[0] ** 2 + unit[1] ** 2) - 1.0) < 1e-9
    assert _l2([0.0, 0.0]) == [0.0, 0.0]


def test_embedding_backend_falls_back_to_tfidf_on_hash_bow(db_session) -> None:
    """With only the hash-BOW baseline, the embedding backend clusters via TF-IDF and says so."""
    shelf = _shelf_with(
        db_session,
        [
            ("Neural machine translation", "attention transformer encoder decoder"),
            ("Deep learning for vision", "convolution image classification network"),
        ],
    )
    result = model_topics(
        db_session, scope_type="shelf", scope_id=shelf.id, max_topics=2, backend="embedding"
    )
    assert result["backend"] == "embedding"
    assert result["used_embeddings"] is False  # honest: no real model available


def test_embedding_backend_uses_dense_vectors_when_model_active(db_session, monkeypatch) -> None:
    """An injected real embedding provider makes the backend cluster on dense vectors."""
    from app.services import embeddings as emb

    class _FakeProvider:
        model_name = "st:fake-dense"

        def embed(self, text: str):
            # Two well-separated regions so clustering has real dense signal.
            return [1.0, 0.0, 0.0] if "translation" in text.lower() else [0.0, 1.0, 0.0]

    monkeypatch.setattr(
        emb,
        "resolve_embedding_provider",
        lambda *a, **k: emb.ResolvedEmbeddingProvider(
            _FakeProvider(), "sentence_transformers", False
        ),
    )
    # Titles chosen so the deterministic k-means seeds (title-order positions 0 and 2) land in
    # different dense clusters: sorted casefold order is [trans, trans, vision, vision].
    shelf = _shelf_with(
        db_session,
        [
            ("Alpha translation model", "translation attention"),
            ("Beta translation model", "translation alignment"),
            ("Yak vision model", "vision convolution"),
            ("Zeta vision model", "vision detection"),
        ],
    )
    result = model_topics(
        db_session, scope_type="shelf", scope_id=shelf.id, max_topics=2, backend="embedding"
    )
    assert result["used_embeddings"] is True
    assert result["embedding_model"] == "st:fake-dense"
    assert len(result["topics"]) == 2
    # The two translation papers cluster together, apart from the two vision papers.
    by_title = _topic_assignments_by_title(db_session, result["model_id"])
    assert (
        by_title["Alpha translation model"]["topic_id"]
        == by_title["Beta translation model"]["topic_id"]
    )
    assert (
        by_title["Alpha translation model"]["topic_id"] != by_title["Yak vision model"]["topic_id"]
    )


# --- D12: multimode clustering enforces per-model dimensions (skip, never pad) ---


def _loose_works(db_session, titles):
    from app.models.work import Work

    works = []
    for title in titles:
        w = Work(canonical_title=title, normalized_title=title.lower())
        db_session.add(w)
        works.append(w)
    db_session.commit()
    return works


def test_paper_dense_vectors_skips_model_on_dim_mismatch(db_session, monkeypatch) -> None:
    """A model emitting an inconsistent per-work dimension is skipped, not padded/truncated (D12)."""
    from app.services import embedding_registry as reg
    from app.services.topic_modeling import _paper_dense_vectors

    works = _loose_works(db_session, ["Alpha paper", "Beta paper", "Gamma paper"])

    class _BadProvider:
        model_name = "st:bad-dims"

        def embed(self, text: str):
            # The "Beta" doc gets a different dimension — a real registry/provider bug.
            return [1.0, 0.0] if "Beta" in text else [1.0, 0.0, 0.0]

    monkeypatch.setattr(reg, "column_for", lambda db, name: None)
    monkeypatch.setattr(reg, "provider_for", lambda db, name, **k: _BadProvider())

    vectors, label = _paper_dense_vectors(db_session, works, "st:bad-dims")
    assert vectors is None  # the only model was skipped → fall back to the TF-IDF baseline
    assert label is None


def test_paper_dense_vectors_keeps_model_with_consistent_dims(db_session, monkeypatch) -> None:
    """A model whose per-work vectors all share one dimension is used normally (D12 happy path)."""
    from app.services import embedding_registry as reg
    from app.services.topic_modeling import _paper_dense_vectors

    works = _loose_works(db_session, ["Alpha paper", "Beta paper"])

    class _GoodProvider:
        model_name = "st:good-dims"

        def embed(self, text: str):
            return [1.0, 0.0, 0.0] if "Alpha" in text else [0.0, 1.0, 0.0]

    monkeypatch.setattr(reg, "column_for", lambda db, name: None)
    monkeypatch.setattr(reg, "provider_for", lambda db, name, **k: _GoodProvider())

    vectors, label = _paper_dense_vectors(db_session, works, "st:good-dims")
    assert label == "st:good-dims"
    assert vectors is not None
    assert all(len(v) == 3 for v in vectors)  # one 3-dim vector per work, nothing padded
