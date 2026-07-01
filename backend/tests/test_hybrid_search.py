"""Hybrid search + RRF fusion tests (HS5)."""

import uuid
from pathlib import Path

import pytest
from app.db.base import Base
from app.models.ai import Embedding
from app.models.chunk import WorkChunk
from app.models.citation import RawTeiDocument
from app.models.work import Work
from app.services.bm25_index import invalidate_cache
from app.services.chunk_search import PaperHit
from app.services.hybrid_search import _fuse, hybrid_search
from app.services.semantic_search import ensure_work_embeddings
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'hybrid.db'}")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            Work.__table__,
            Embedding.__table__,
            WorkChunk.__table__,
            RawTeiDocument.__table__,
        ],
    )
    with sessionmaker(bind=engine, autocommit=False, autoflush=False)() as session:
        yield session


# --- RRF fusion -------------------------------------------------------------


def test_rrf_prefers_papers_ranked_by_both_engines() -> None:
    a = Work(id=uuid.uuid4(), canonical_title="A")
    b = Work(id=uuid.uuid4(), canonical_title="B")
    c = Work(id=uuid.uuid4(), canonical_title="C")
    lexical = [PaperHit(work=a, score=5.0), PaperHit(work=b, score=4.0)]  # ranks A=1, B=2
    semantic = [
        PaperHit(work=b, score=0.9, passage="p", section="Methods"),
        PaperHit(work=c, score=0.8),
    ]
    fused = _fuse(lexical, semantic, limit=10)
    assert fused[0].work.id == b.id  # in both -> highest RRF
    assert fused[0].passage == "p"  # passage carried from the semantic side
    assert {h.work.id for h in fused} == {a.id, b.id, c.id}
    # B's ranks recorded from both engines.
    assert fused[0].lexical_rank == 2
    assert fused[0].semantic_rank == 1


# --- modes (service level; semantic falls back to doc-level on SQLite) -------


def _seed(db) -> list[Work]:
    works = [
        Work(
            canonical_title="Graph neural networks",
            normalized_title="gnn",
            abstract="Message passing over graph structured data.",
        ),
        Work(
            canonical_title="Sourdough baking",
            normalized_title="bread",
            abstract="Fermenting and baking artisan bread.",
        ),
    ]
    db.add_all(works)
    db.commit()
    ensure_work_embeddings(db)
    db.commit()
    return works


def test_hybrid_modes_return_results_and_respect_visibility(db_session) -> None:
    invalidate_cache()
    works = _seed(db_session)
    for mode in ("lexical", "semantic", "hybrid"):
        hits = hybrid_search(
            db_session, "graph message passing", visible_ids=None, mode=mode, limit=5
        )
        assert hits, f"{mode} returned nothing"
        assert hits[0].work.canonical_title == "Graph neural networks"
    only_first = hybrid_search(
        db_session, "graph message passing", visible_ids={works[0].id}, mode="hybrid", limit=5
    )
    assert all(h.work.id == works[0].id for h in only_first)


def test_hybrid_empty_query_is_empty(db_session) -> None:
    _seed(db_session)
    assert hybrid_search(db_session, "  ", visible_ids=None, mode="hybrid") == []


# --- API --------------------------------------------------------------------


def test_search_api_hybrid_default(client, auth_headers, db) -> None:
    db.add(
        Work(
            canonical_title="Graph neural networks",
            normalized_title="gnn",
            abstract="message passing over graphs",
        )
    )
    db.commit()
    client.post("/api/v1/search/reindex", headers=auth_headers("editor"))
    r = client.post(
        "/api/v1/search",
        headers=auth_headers("reader"),
        json={"q": "graph message passing", "mode": "hybrid"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "hybrid"
    assert body["items"]
    assert body["items"][0]["title"] == "Graph neural networks"
    assert body["embedding_provider_used"] == "hash-bow-v1"  # provenance for hybrid/semantic


def test_search_api_lexical_has_no_provenance(client, auth_headers, db) -> None:
    db.add(Work(canonical_title="Quantum entanglement", normalized_title="q", abstract="physics"))
    db.commit()
    r = client.post(
        "/api/v1/search",
        headers=auth_headers("reader"),
        json={"q": "quantum", "mode": "lexical"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["items"][0]["title"] == "Quantum entanglement"
    assert body["embedding_provider_used"] is None
    assert body["degraded"] is False


def test_search_relevance_is_normalized_across_modes(client, auth_headers, db) -> None:
    """#20: raw scores differ wildly by mode; the display relevance is always in [0,1], top=1."""
    for i in range(3):
        db.add(
            Work(
                canonical_title=f"Graph neural networks {i}",
                normalized_title=f"gnn {i}",
                abstract="message passing over graphs and nodes and edges",
            )
        )
    db.commit()
    client.post("/api/v1/search/reindex", headers=auth_headers("editor"))
    for mode in ("lexical", "semantic", "hybrid"):
        r = client.post(
            "/api/v1/search",
            headers=auth_headers("reader"),
            json={"q": "graph message passing", "mode": mode},
        )
        assert r.status_code == 200, mode
        items = r.json()["items"]
        assert items, mode
        rels = [it["relevance"] for it in items]
        assert all(0.0 <= x <= 1.0 for x in rels), (mode, rels)
        assert max(rels) <= 1.0 and rels[0] == max(rels), (mode, rels)
