"""Annotation search + export (SPEC §8.8.7 / §8.17.4)."""


def _work(client, h, title):
    return client.post("/api/v1/works", headers=h, json={"canonical_title": title}).json()["id"]


def _annotate(client, h, work_id, **kw):
    return client.post(f"/api/v1/works/{work_id}/annotations", headers=h, json=kw)


def test_annotation_search_across_works(client, auth_headers):
    h = auth_headers("editor")
    w1 = _work(client, h, "Paper One")
    w2 = _work(client, h, "Paper Two")
    _annotate(
        client,
        h,
        w1,
        annotation_type="note",
        content_markdown="key insight about transformers",
        page=3,
    )
    _annotate(
        client, h, w2, annotation_type="highlight", selected_text="sourdough fermentation", page=1
    )

    hits = client.get("/api/v1/works/annotations/search?q=transformers", headers=h)
    assert hits.status_code == 200
    body = hits.json()
    assert len(body) == 1
    assert body[0]["content_markdown"] == "key insight about transformers"

    typed = client.get(
        "/api/v1/works/annotations/search?annotation_type=highlight", headers=h
    ).json()
    assert all(a["annotation_type"] == "highlight" for a in typed)


def test_annotation_export_markdown(client, auth_headers):
    h = auth_headers("editor")
    w = _work(client, h, "Exportable")
    _annotate(client, h, w, annotation_type="note", content_markdown="note body", page=2)
    r = client.get(f"/api/v1/works/{w}/annotations/export?format=markdown", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["filename"].endswith(".md")
    assert "Annotations — Exportable" in body["content"]
    assert "note body" in body["content"]
