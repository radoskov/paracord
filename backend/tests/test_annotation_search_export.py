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


def test_annotation_delete(client, auth_headers):
    h = auth_headers("editor")
    w = _work(client, h, "Deletable")
    ann = _annotate(client, h, w, annotation_type="note", content_markdown="to remove", page=1)
    assert ann.status_code == 201
    ann_id = ann.json()["id"]

    deleted = client.delete(f"/api/v1/works/{w}/annotations/{ann_id}", headers=h)
    assert deleted.status_code == 204

    # Re-deleting the now-missing annotation is a 404.
    again = client.delete(f"/api/v1/works/{w}/annotations/{ann_id}", headers=h)
    assert again.status_code == 404


def test_annotation_delete_cross_work_404(client, auth_headers):
    h = auth_headers("editor")
    w1 = _work(client, h, "Owner Paper")
    w2 = _work(client, h, "Other Paper")
    ann = _annotate(client, h, w1, annotation_type="note", content_markdown="belongs to w1", page=1)
    ann_id = ann.json()["id"]

    # Deleting via the wrong work_id must 404 and leave the annotation intact.
    wrong = client.delete(f"/api/v1/works/{w2}/annotations/{ann_id}", headers=h)
    assert wrong.status_code == 404
    still = client.delete(f"/api/v1/works/{w1}/annotations/{ann_id}", headers=h)
    assert still.status_code == 204


# A6: annotation_type is constrained to the SPEC §8.8.5/§9.3 enumerated set.
_VALID_ANNOTATION_TYPES = ("highlight", "note", "page_anchor", "citation_note", "tag_note")


def test_annotation_type_valid_values_accepted(client, auth_headers):
    h = auth_headers("editor")
    w = _work(client, h, "Typed Paper")
    for atype in _VALID_ANNOTATION_TYPES:
        resp = _annotate(client, h, w, annotation_type=atype, content_markdown="body", page=1)
        assert resp.status_code == 201, (atype, resp.text)
        assert resp.json()["annotation_type"] == atype


def test_annotation_type_invalid_value_rejected(client, auth_headers):
    h = auth_headers("editor")
    w = _work(client, h, "Rejecting Paper")
    resp = _annotate(client, h, w, annotation_type="scribble", content_markdown="body", page=1)
    assert resp.status_code == 422, resp.text
