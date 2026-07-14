"""Embedding-similarity (topic) graph endpoint (#6)."""

from app.models.work import Work


def _dense_provider(monkeypatch):
    from app.services import embeddings as emb

    class _Fake:
        model_name = "st:fake-dense"

        def embed(self, text: str):
            return [1.0, 0.0] if "translation" in text.lower() else [0.0, 1.0]

    monkeypatch.setattr(
        emb,
        "resolve_embedding_provider",
        lambda *a, **k: emb.ResolvedEmbeddingProvider(_Fake(), "sentence_transformers", False),
    )


def test_topic_graph_links_similar_papers(client, auth_headers, db, monkeypatch):
    _dense_provider(monkeypatch)
    for t in ("Neural translation", "Statistical translation", "Image vision", "Object vision"):
        db.add(Work(canonical_title=t, normalized_title=t.lower(), abstract=t))
    db.commit()

    r = client.post(
        "/api/v1/graphs/topic",
        headers=auth_headers("reader"),
        json={"scope": {"type": "library"}, "min_similarity": 0.5},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["used_embeddings"] is True
    assert body["summary"]["node_count"] == 4
    # Identical-direction vectors within each theme → similarity 1.0 edges exist.
    assert body["edges"]
    assert all(0.0 <= e["weight"] <= 1.0 for e in body["edges"])


def test_topic_graph_without_real_model_is_honest(client, auth_headers, db):
    """Default hash-BOW → no dense semantics → empty edges + a note, not a lexical graph."""
    db.add(Work(canonical_title="Solo", normalized_title="solo", abstract="text"))
    db.commit()
    r = client.post(
        "/api/v1/graphs/topic", headers=auth_headers("reader"), json={"scope": {"type": "library"}}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["used_embeddings"] is False
    assert body["edges"] == []
    assert "note" in body["summary"]


def _reference_edges(works, vectors, *, k, min_similarity):
    """Prior O(n^2) pure-Python kNN cosine edges — the D20 numpy op must reproduce this exactly."""
    from app.services.vector_math import sparse_cosine

    n = len(works)
    edge_weight: dict[tuple[str, str], float] = {}
    for i in range(n):
        sims = [(j, sparse_cosine(vectors[i], vectors[j])) for j in range(n) if j != i]
        sims.sort(key=lambda t: t[1], reverse=True)
        for j, sim in sims[:k]:
            if sim < min_similarity:
                break
            a, b = sorted((str(works[i].id), str(works[j].id)))
            if sim > edge_weight.get((a, b), 0.0):
                edge_weight[(a, b)] = sim
    return {(a, b): round(w, 4) for (a, b), w in edge_weight.items()}


def test_knn_edges_numpy_matches_pure_python(db, monkeypatch):
    """D20: the vectorized numpy kNN produces exactly the same edges as the old Python loop."""
    from app.services import topic_graph as tg

    works = []
    for t in ("Alpha", "Beta", "Gamma", "Delta", "Epsilon"):
        w = Work(canonical_title=t, normalized_title=t.lower(), abstract=t)
        db.add(w)
        works.append(w)
    db.commit()
    # Well-separated fixture vectors (no ties near the threshold) so both methods agree exactly.
    raw = [
        [1.0, 0.0, 0.0],
        [0.9, 0.1, 0.0],
        [0.0, 1.0, 0.0],
        [0.0, 0.95, 0.05],
        [0.0, 0.0, 1.0],
    ]
    vectors = [{i: x for i, x in enumerate(vec)} for vec in raw]

    edges = tg._knn_edges(works, vectors, k=3, min_similarity=0.3)
    got = {(e.source, e.target): e.weight for e in edges}
    assert got == _reference_edges(works, vectors, k=3, min_similarity=0.3)


def test_topic_graph_skips_unindexed_papers(db, monkeypatch):
    """D19: papers with no pre-indexed vector are skipped (not embedded inline) + counted."""
    from app.services import topic_graph as tg

    works = []
    for t in ("Alpha", "Beta", "Gamma"):
        w = Work(canonical_title=t, normalized_title=t.lower(), abstract=t)
        db.add(w)
        works.append(w)
    db.commit()

    kept = works[:2]
    vectors = [{0: 1.0, 1: 0.0}, {0: 0.99, 1: 0.01}]

    def _fake_dense(db_, all_works, embedding_model):
        # Two of three papers are indexed; the third is skipped (un-indexed for this model).
        return vectors, [w for w in all_works if w.id in {kw.id for kw in kept}], "st:fake", 1

    monkeypatch.setattr(tg, "_paper_dense_vectors", _fake_dense)
    graph = tg.build_topic_graph(db, scope_type="library", min_similarity=0.3)
    assert graph.summary["used_embeddings"] is True
    assert graph.summary["node_count"] == 2  # only the indexed papers become nodes
    assert graph.summary["unindexed_works"] == 1
    assert "note" in graph.summary
    assert {n.work_id for n in graph.nodes} == {kw.id for kw in kept}
