"""Related papers (§8.17.2) + topic accept-as-tag / create-shelf (§8.15.3)."""


def _work(client, h, title, abstract):
    return client.post(
        "/api/v1/works", headers=h, json={"canonical_title": title, "abstract": abstract}
    ).json()["id"]


def test_related_papers_ranks_similar_first(client, auth_headers):
    h = auth_headers("editor")
    a = _work(client, h, "Transformer attention", "self attention transformer sequence model")
    _work(client, h, "Attention networks", "attention mechanism transformer neural sequence")
    _work(client, h, "Sourdough baking", "bread fermentation yeast flour oven")
    client.post("/api/v1/search/reindex", headers=h)  # build embeddings off the read path

    related = client.get(f"/api/v1/works/{a}/related?limit=2", headers=h)
    assert related.status_code == 200
    titles = [w["canonical_title"] for w in related.json()]
    assert titles  # at least one neighbor
    assert a not in [w["id"] for w in related.json()]  # never returns itself
    assert "Attention networks" in titles  # the similar one ranks in


def test_topic_accept_as_tag_and_create_shelf(client, auth_headers, db):
    h = auth_headers("editor")
    # A small two-cluster corpus on a shelf.
    from app.models.organization import Shelf, ShelfWork
    from app.models.work import Work

    shelf = Shelf(name="topic-src")
    db.add(shelf)
    db.flush()
    for title, abstract in [
        ("Transformer pruning", "attention heads transformer pruning latency"),
        ("Model distillation", "knowledge distillation language model compression"),
        ("Sourdough microbiome", "lactic bacteria yeast sourdough fermentation"),
        ("Whole grain bread", "hydration oven bread crumb crust texture"),
    ]:
        w = Work(canonical_title=title, normalized_title=title.lower(), abstract=abstract)
        db.add(w)
        db.flush()
        db.add(ShelfWork(shelf_id=shelf.id, work_id=w.id))
    db.commit()

    model = client.post(
        "/api/v1/ai/topics",
        headers=h,
        json={
            "scope_type": "shelf",
            "scope_id": str(shelf.id),
            "max_topics": 2,
            "backend": "embedding",
        },
    ).json()
    model_id = model["model_id"]
    topic_id = model["topics"][0]["topic_id"]

    tagged = client.post(
        "/api/v1/ai/topics/accept-as-tag",
        headers=h,
        json={"topic_model_id": model_id, "topic_id": topic_id, "name": "ml-topic"},
    )
    assert tagged.status_code == 201
    assert tagged.json()["tagged"] >= 1

    shelved = client.post(
        "/api/v1/ai/topics/create-shelf",
        headers=h,
        json={"topic_model_id": model_id, "topic_id": topic_id, "name": "Topic shelf"},
    )
    assert shelved.status_code == 201
    assert shelved.json()["added"] >= 1
