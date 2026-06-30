"""Semantic search + embedding tests (M7)."""

from pathlib import Path

import pytest
from app.db.base import Base
from app.models.ai import Embedding
from app.models.work import Work
from app.services.embeddings import cosine_similarity, embed_text
from app.services.semantic_search import ensure_work_embeddings, semantic_search
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'semantic.db'}")
    Base.metadata.create_all(bind=engine, tables=[Work.__table__, Embedding.__table__])
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with session_local() as session:
        yield session


# --- embedder ---------------------------------------------------------------


def test_embed_text_is_deterministic_and_normalized() -> None:
    a = embed_text("attention mechanism transformer")
    b = embed_text("attention mechanism transformer")
    assert a == b  # deterministic (hashlib, not salted hash())
    assert abs(sum(v * v for v in a) ** 0.5 - 1.0) < 1e-9  # L2-normalized
    assert cosine_similarity(a, b) == pytest.approx(1.0)


def test_cosine_similarity_orders_related_text_higher() -> None:
    query = embed_text("neural attention mechanisms")
    related = embed_text("an attention mechanism for neural translation")
    unrelated = embed_text("baking sourdough bread at home")
    assert cosine_similarity(query, related) > cosine_similarity(query, unrelated)


# --- semantic_search --------------------------------------------------------


def _seed(db) -> None:
    db.add_all(
        [
            Work(
                canonical_title="Attention Is All You Need",
                normalized_title="attention",
                abstract="A transformer architecture based purely on attention mechanisms.",
            ),
            Work(
                canonical_title="Deep Residual Learning",
                normalized_title="resnet",
                abstract="Residual connections for training very deep convolutional networks.",
            ),
            Work(
                canonical_title="Sourdough Baking",
                normalized_title="bread",
                abstract="Techniques for fermenting and baking artisan bread at home.",
            ),
        ]
    )
    db.commit()


def test_semantic_search_ranks_relevant_work_first(db_session) -> None:
    _seed(db_session)
    # auto_index builds embeddings for this direct-call test; the API path is read-only.
    hits = semantic_search(db_session, "transformer attention model", limit=3, auto_index=True)
    assert hits, "expected at least one hit"
    assert hits[0].work.canonical_title == "Attention Is All You Need"
    # Scores are sorted descending.
    assert all(hits[i].score >= hits[i + 1].score for i in range(len(hits) - 1))


def test_semantic_search_is_read_only_without_index(db_session) -> None:
    """A search must not write embeddings (H2): with none indexed, embedding mode returns empty."""
    from app.models.ai import Embedding

    _seed(db_session)
    assert semantic_search(db_session, "transformer attention") == []
    assert db_session.scalar(select(func.count()).select_from(Embedding)) == 0


def test_lexical_search_needs_no_embeddings(db_session) -> None:
    _seed(db_session)
    hits = semantic_search(db_session, "transformer attention", mode="lexical")
    assert hits
    assert hits[0].work.canonical_title == "Attention Is All You Need"


def test_search_lazily_indexes_then_caches(db_session) -> None:
    _seed(db_session)
    # First call embeds all three works.
    added = ensure_work_embeddings(db_session)
    db_session.commit()
    assert added == 3
    assert db_session.scalar(select(func.count()).select_from(Embedding)) == 3
    # Re-running adds nothing (cached).
    assert ensure_work_embeddings(db_session) == 0


def test_semantic_search_empty_query_returns_empty(db_session) -> None:
    _seed(db_session)
    assert semantic_search(db_session, "   ") == []


def test_semantic_search_on_empty_library_is_empty(db_session) -> None:
    assert semantic_search(db_session, "anything") == []


# --- API --------------------------------------------------------------------


def test_semantic_search_api(client, auth_headers, db) -> None:
    db.add(
        Work(
            canonical_title="Graph Neural Networks",
            normalized_title="gnn",
            abstract="Message passing over graph structured data.",
        )
    )
    db.commit()
    # Build embeddings off the read path first (reindex requires editor+).
    reindex = client.post("/api/v1/search/reindex", headers=auth_headers("editor"))
    assert reindex.status_code == 200
    r = client.post(
        "/api/v1/search/semantic",
        headers=auth_headers("reader"),
        json={"q": "graph message passing"},
    )
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["items"], list)
    assert body["items"][0]["title"] == "Graph Neural Networks"
    assert body["items"][0]["score"] > 0


def test_reindex_requires_editor(client, auth_headers) -> None:
    assert client.post("/api/v1/search/reindex", headers=auth_headers("reader")).status_code == 403
    assert client.post("/api/v1/search/reindex", headers=auth_headers("editor")).status_code == 200
