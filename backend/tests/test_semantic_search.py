"""Semantic search + embedding tests (M7)."""

from pathlib import Path

import pytest
from app.db.base import Base
from app.models.ai import Embedding
from app.models.work import Work
from app.services.embeddings import HashBowProvider, cosine_similarity, embed_many, embed_text
from app.services.semantic_search import ensure_work_embeddings, reindex_status, semantic_search
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'semantic.db'}")
    Base.metadata.create_all(bind=engine, tables=[Work.__table__, Embedding.__table__])
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with session_local() as session:
        yield session


# --- query-embedding LRU cache ----------------------------------------------


def test_embed_query_caches_per_model_and_can_disable() -> None:
    from app.services.embeddings import embed_query

    class CountingProvider:
        def __init__(self, name):
            self.model_name = name
            self.calls = 0

        def embed(self, text):
            self.calls += 1
            return [float(len(text)), float(self.calls)]

    p = CountingProvider("ollama:nomic-embed-text:latest")
    # First call embeds; the identical (model, query) is served from cache.
    v1 = embed_query(p, "attention", cache_size=2048)
    v2 = embed_query(p, "attention", cache_size=2048)
    assert v1 == v2
    assert p.calls == 1  # second query hit the cache, not the provider

    # A different model_name is a distinct key (no cross-model contamination).
    other = CountingProvider("ollama:bge-m3:latest")
    embed_query(other, "attention", cache_size=2048)
    assert other.calls == 1

    # cache_size=0 disables caching → every call re-embeds.
    fresh = CountingProvider("ollama:nomic-embed-text:latest")
    embed_query(fresh, "same", cache_size=0)
    embed_query(fresh, "same", cache_size=0)
    assert fresh.calls == 2


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


def test_semantic_search_filters_to_visible_exactly(db_session) -> None:
    """visible_ids restricts results before truncation — no hidden work leaks (HS3)."""
    _seed(db_session)
    ensure_work_embeddings(db_session)
    db_session.commit()
    works = db_session.scalars(select(Work)).all()
    visible = {works[0].id}
    hits = semantic_search(db_session, "transformer attention", visible_ids=visible, limit=10)
    assert hits
    assert all(h.work.id in visible for h in hits)


def test_lexical_search_filters_to_visible_exactly(db_session) -> None:
    _seed(db_session)
    works = db_session.scalars(select(Work)).all()
    visible = {works[0].id}
    hits = semantic_search(db_session, "transformer attention", mode="lexical", visible_ids=visible)
    assert hits
    assert all(h.work.id in visible for h in hits)


def test_semantic_search_papers_falls_back_to_doc_level_on_sqlite(db_session) -> None:
    """With no chunk column (SQLite / hash-BOW), the paper-level engine uses the doc baseline."""
    from app.services.chunk_search import semantic_search_papers

    _seed(db_session)
    ensure_work_embeddings(db_session)
    db_session.commit()
    hits = semantic_search_papers(db_session, "transformer attention", visible_ids=None, limit=3)
    assert hits
    assert hits[0].work.canonical_title == "Attention Is All You Need"
    assert hits[0].passage is None  # no chunk passage in the doc-level fallback


def test_semantic_search_empty_query_returns_empty(db_session) -> None:
    _seed(db_session)
    assert semantic_search(db_session, "   ") == []


def test_semantic_search_on_empty_library_is_empty(db_session) -> None:
    assert semantic_search(db_session, "anything") == []


# --- API --------------------------------------------------------------------


def test_semantic_search_api(client, auth_headers, db, monkeypatch) -> None:
    from app.workers import queue as queue_module

    db.add(
        Work(
            canonical_title="Graph Neural Networks",
            normalized_title="gnn",
            abstract="Message passing over graph structured data.",
        )
    )
    db.commit()
    # Build embeddings off the read path first (reindex requires editor+). With the queue reachable
    # reindex is queued (D14) and a worker can't see this in-request DB, so force the synchronous
    # fallback (queue unavailable) to build embeddings inline for the assertion below.
    monkeypatch.setattr(queue_module, "enqueue_reindex", lambda: None)
    reindex = client.post("/api/v1/search/reindex", headers=auth_headers("editor"))
    assert reindex.status_code == 200
    assert reindex.json()["queued"] is False
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


# --- D14: batched embedding + queued reindex --------------------------------


def test_embed_many_matches_per_text_embed() -> None:
    """D14: batch embedding returns the same vectors as one-at-a-time ``embed``, in order."""
    texts = ["attention transformer", "graph neural network", ""]
    provider = HashBowProvider()
    batched = embed_many(provider, texts)
    assert batched == [provider.embed(t) for t in texts]


def test_embed_many_uses_provider_batch_path_once() -> None:
    """D14: a provider that exposes ``embed_many`` is called once for the whole batch, not per text."""
    calls = {"batch": 0, "single": 0}

    class _BatchProvider:
        model_name = "st:fake-batch"

        def embed(self, text: str):
            calls["single"] += 1
            return [0.0]

        def embed_many(self, texts):
            calls["batch"] += 1
            return [[float(len(t))] for t in texts]

    vectors = embed_many(_BatchProvider(), ["a", "bb", "ccc"])
    assert vectors == [[1.0], [2.0], [3.0]]
    assert calls == {"batch": 1, "single": 0}  # one batched round-trip, no per-text fan-out


def test_reindex_enqueues_when_queue_available(client, auth_headers, db, monkeypatch) -> None:
    """D14: with the queue reachable, /search/reindex enqueues the job instead of embedding inline."""
    from app.api.v1.endpoints import search as search_endpoint
    from app.workers import queue as queue_module

    db.add(Work(canonical_title="Queued Paper", normalized_title="qp", abstract="text"))
    db.commit()

    monkeypatch.setattr(queue_module, "enqueue_reindex", lambda: "reindex-job-1")
    called = {"inline": False}
    monkeypatch.setattr(
        search_endpoint,
        "ensure_work_embeddings",
        lambda *a, **k: called.__setitem__("inline", True) or 0,
    )

    r = client.post("/api/v1/search/reindex", headers=auth_headers("editor"))
    assert r.status_code == 200
    body = r.json()
    assert body["queued"] is True
    assert body["job_id"] == "reindex-job-1"
    assert body["status"] == "queued"
    assert called["inline"] is False  # the pipeline did NOT run in-request
    # No embeddings were written by the request (the worker builds them).
    assert db.scalar(select(func.count()).select_from(Embedding)) == 0


# --- provider-fallback provenance (Phase B2) --------------------------------


def test_semantic_search_reports_used_provider_when_default(client, auth_headers, db) -> None:
    """The response names the embedding provider actually used and is not marked degraded when the
    built-in hash-BOW baseline is what was requested."""
    db.add(Work(canonical_title="A Paper", normalized_title="a", abstract="Some indexable text."))
    db.commit()
    client.post("/api/v1/search/reindex", headers=auth_headers("editor"))
    r = client.post(
        "/api/v1/search/semantic",
        headers=auth_headers("reader"),
        json={"q": "indexable text"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["embedding_provider_used"] == "hash-bow-v1"
    assert body["embedding_provider_requested"] == "hash_bow"
    assert body["degraded"] is False


def test_semantic_search_reports_degraded_on_provider_fallback(
    client, auth_headers, db, monkeypatch
) -> None:
    """When a non-default provider is configured but unavailable, the search silently uses hash-BOW
    and the response reports the degradation so the UI can surface 'requested X, using Y'."""
    from app.services import ai_config

    real = ai_config.get_ai_config

    def _degraded_cfg(db_, *, settings=None):
        cfg = real(db_, settings=settings)
        cfg.embedding_provider = "sentence_transformers"
        cfg.embedding_model = "definitely-not-installed-model"
        return cfg

    monkeypatch.setattr(ai_config, "get_ai_config", _degraded_cfg)

    r = client.post(
        "/api/v1/search/semantic",
        headers=auth_headers("reader"),
        json={"q": "anything"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["embedding_provider_requested"] == "sentence_transformers"
    assert body["embedding_provider_used"] == "hash-bow-v1"
    assert body["degraded"] is True


def test_semantic_search_lexical_mode_has_no_provider_provenance(client, auth_headers, db) -> None:
    r = client.post(
        "/api/v1/search/semantic",
        headers=auth_headers("reader"),
        json={"q": "anything", "mode": "lexical"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["embedding_provider_used"] is None
    assert body["degraded"] is False


# --- #2: Ollama model-name canonicalization ---------------------------------


def test_normalize_ollama_model_appends_latest_tag() -> None:
    from app.services.embeddings import normalize_ollama_model

    assert normalize_ollama_model("nomic-embed-text") == "nomic-embed-text:latest"
    # An explicit tag is preserved; a fully-qualified name is untouched.
    assert normalize_ollama_model("nomic-embed-text:v1.5") == "nomic-embed-text:v1.5"
    assert normalize_ollama_model("  mxbai-embed-large  ") == "mxbai-embed-large:latest"
    assert normalize_ollama_model("") == ""


def test_ollama_provider_canonicalizes_wire_and_key() -> None:
    """The bare name and its :latest tag are the same model, so both the daemon call and the stored
    model_name key use the tagged form — this is the actual #2 silent-failure fix."""
    from app.services.embeddings import OllamaProvider

    bare = OllamaProvider("nomic-embed-text", "http://ollama:11434")
    tagged = OllamaProvider("nomic-embed-text:latest", "http://ollama:11434")
    assert bare.model_name == "ollama:nomic-embed-text:latest"
    assert bare._model == "nomic-embed-text:latest"
    assert bare.model_name == tagged.model_name  # unified vector namespace


def test_nomic_keeps_its_chunk_column_after_canonicalization() -> None:
    """The chunk-column registry key was updated to the canonical name so nomic keeps vec_nomic."""
    from app.services.chunk_embeddings import chunk_column_for

    assert chunk_column_for("ollama:nomic-embed-text:latest") == ("vec_nomic", 768)


def test_cached_provider_memoizes_per_key_and_evicts() -> None:
    """Providers are built once per (kind, model, url) and dropped when the model is removed."""
    from app.services.embeddings import cached_provider, evict_cached_providers

    first = cached_provider("ollama", "nomic-embed-text", "http://ollama:11434")
    assert cached_provider("ollama", "nomic-embed-text", "http://ollama:11434") is first
    evict_cached_providers(first.model_name)
    rebuilt = cached_provider("ollama", "nomic-embed-text", "http://ollama:11434")
    assert rebuilt is not first
    evict_cached_providers(rebuilt.model_name)


def test_reindex_status_counts_current_papers_not_stale_rows(db_session) -> None:
    """``indexed`` must stay a subset of ``total``: a stale embedding left by a deleted/merged
    paper, or a merged shadow's own embedding, must never push it above the total (the bug that
    produced the nonsensical "7 / 3 papers indexed")."""
    import uuid as _uuid

    provider = HashBowProvider()
    a = Work(canonical_title="Alpha", normalized_title="alpha", year=2020)
    b = Work(canonical_title="Beta", normalized_title="beta", year=2021)
    db_session.add_all([a, b])
    db_session.commit()
    ensure_work_embeddings(db_session)  # embeds both current papers for the active model
    db_session.commit()

    # A stale embedding whose work no longer exists (deleted/merged) — a raw-row count would inflate.
    db_session.add(
        Embedding(
            entity_type="work",
            entity_id=_uuid.uuid4(),
            model_name=provider.model_name,
            dim=2,
            vector=[0.1, 0.2],
        )
    )
    # A merged shadow with text + its own embedding is not a current paper.
    shadow = Work(
        canonical_title="Gamma dup",
        normalized_title="gamma dup",
        year=2019,
        merged_into_id=a.id,
    )
    db_session.add(shadow)
    db_session.commit()
    db_session.add(
        Embedding(
            entity_type="work",
            entity_id=shadow.id,
            model_name=provider.model_name,
            dim=2,
            vector=[0.3, 0.4],
        )
    )
    db_session.commit()

    status = reindex_status(db_session, provider=provider)
    assert status["total"] == 2  # Alpha + Beta; the merged shadow is excluded
    assert status["indexed"] == 2  # both embedded; the stale row and shadow row are not counted
    assert status["indexed"] <= status["total"]
