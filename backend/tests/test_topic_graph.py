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
