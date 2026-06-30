"""Acceptance tests for the embedding/BERTopic-style topic backend (Stage 6).

The ``embedding``/``bertopic`` backend reuses the deterministic TF-IDF + k-means clustering and
layers the richer result shape (representative works, coherence, optional outliers + hierarchy) on
top, echoing the requested embedding model for provenance. A real sentence-transformers/BERTopic
implementation can drop in behind the same contract; these tests pin that contract.
"""

from pathlib import Path

import pytest  # noqa: F401  (kept for fixture decorators below)
from app.db.base import Base
from app.models.ai import TopicAssignment
from app.models.organization import Shelf, ShelfWork
from app.models.work import Work
from app.services.topic_modeling import model_topics
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

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


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'future_topics.db'}")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            Work.__table__,
            Shelf.__table__,
            ShelfWork.__table__,
            TopicAssignment.__table__,
        ],
    )
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with session_local() as session:
        yield session


def _shelf_with(db, works) -> Shelf:
    shelf = Shelf(name="Future topic scope")
    db.add(shelf)
    db.flush()

    for title, abstract in works:
        work = Work(canonical_title=title, normalized_title=title.lower(), abstract=abstract)
        db.add(work)
        db.flush()
        db.add(ShelfWork(shelf_id=shelf.id, work_id=work.id))

    db.commit()
    return shelf


def test_future_embedding_backend_returns_provenance_and_representative_papers(db_session) -> None:
    """Future topic results should expose provenance and representative works."""

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


def test_future_embedding_backend_is_stable_across_repeated_runs(db_session) -> None:
    """Future stochastic backends should expose deterministic seeded runs."""

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


def test_future_embedding_backend_supports_hierarchical_or_outlier_metadata(db_session) -> None:
    """Future BERTopic-style metadata should be optional but structured."""

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


def test_future_embedding_backend_degrades_gracefully_on_tiny_scopes(db_session) -> None:
    """Future heavy backends should fall back rather than error on tiny corpora."""

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
