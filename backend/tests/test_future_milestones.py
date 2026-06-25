"""Forward-looking tests for milestones that are not implemented yet (M3 and beyond).

WHY THESE EXIST
    We already know a lot about what M3+ must do (SPECIFICATION.md §8/§10). Encoding that
    as tests now (a) records the intended contract, and (b) gives the implementing agent a
    ready-made acceptance check. Each test is SKIPPED until its feature lands.

HOW TO ENABLE (for the agent implementing the milestone)
    1. Search this file for the `ENABLE WHEN` note on the test you are unblocking.
    2. Remove that test's `@pytest.mark.skip(...)` decorator.
    3. Run it; adjust the assertions to the final endpoint/response contract if it differs
       from the spec sketch here, then keep it green.
    Treat enabling the relevant test(s) as part of the Definition of Done for the milestone.

These use the shared API fixtures from conftest.py (client, auth_headers, db, ...).
"""

import pytest

# --- M3: reader, annotations, exports (SPEC §8.8, §8.13, §10.8) -------------


def test_export_shelf_as_bibtex(client, auth_headers, db):
    from app.models.organization import Shelf, ShelfWork
    from app.models.work import Work

    owner = auth_headers("editor")
    work = Work(canonical_title="A Paper", normalized_title="a paper", year=2020, doi="10.1/x")
    shelf = Shelf(name="Refs")
    db.add_all([work, shelf])
    db.flush()
    db.add(ShelfWork(shelf_id=shelf.id, work_id=work.id))
    db.commit()

    r = client.post(
        "/api/v1/exports",
        headers=owner,
        json={"target_type": "shelf", "target_id": str(shelf.id), "format": "bibtex"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "@" in body["content"]  # a BibTeX entry
    assert "A Paper" in body["content"]


def test_create_and_list_annotation(client, auth_headers, db):
    from app.models.work import Work

    h = auth_headers("editor")
    work = Work(canonical_title="Readable", normalized_title="readable")
    db.add(work)
    db.commit()

    created = client.post(
        f"/api/v1/works/{work.id}/annotations",
        headers=h,
        json={"annotation_type": "highlight", "page": 1, "selected_text": "key claim"},
    )
    assert created.status_code == 201
    listed = client.get(f"/api/v1/works/{work.id}/annotations", headers=h).json()
    assert any(a["selected_text"] == "key claim" for a in listed)


@pytest.mark.skip(
    reason="M3 import: ENABLE WHEN POST /imports/bibtex ingests BibTeX (SPEC §8.1/§10.4)"
)
def test_import_bibtex_creates_works(client, auth_headers):
    h = auth_headers("editor")
    bibtex = "@article{vaswani2017, title={Attention Is All You Need}, year={2017}}"
    r = client.post("/api/v1/imports/bibtex", headers=h, json={"content": bibtex})
    assert r.status_code == 201
    works = client.get("/api/v1/works?q=Attention", headers=h).json()
    assert any(w["canonical_title"] == "Attention Is All You Need" for w in works)


# --- M5: local agent enrollment (SPEC §11) ---------------------------------


@pytest.mark.skip(
    reason="M5 agent: ENABLE WHEN agent enrollment is implemented (SPEC §11.2, owner approval)"
)
def test_agent_enrollment_requires_owner_approval(client, auth_headers):
    owner = auth_headers("owner")
    # Owner mints an enrollment token, agent enrolls, owner approves, scoped token issued.
    token = client.post("/api/v1/admin/agents/enroll-token", headers=owner).json()["token"]
    enrolled = client.post("/api/v1/agents/enroll-request", json={"token": token, "name": "laptop"})
    assert enrolled.status_code == 202  # pending until owner approves
    agent_id = enrolled.json()["agent_id"]
    approved = client.post(f"/api/v1/admin/agents/{agent_id}/approve", headers=owner)
    assert approved.status_code == 200


# --- M6: citation graph + summaries (SPEC §8.9, §8.11, §10.7) --------------


def test_shelf_citation_graph_is_scoped(client, auth_headers, db):
    from app.models.organization import Shelf

    h = auth_headers("editor")
    shelf = Shelf(name="Scope")
    db.add(shelf)
    db.commit()
    r = client.post(
        "/api/v1/graphs/citation",
        headers=h,
        json={"scope": {"type": "shelf", "id": str(shelf.id)}, "node_mode": "local_only"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "nodes" in body and "edges" in body and "summary" in body


# --- M7: local AI summaries + topics (SPEC §8.14, §8.15) -------------------


@pytest.mark.skip(
    reason="M7 summaries: ENABLE WHEN local summary pipeline stores provenance (SPEC §8.14)"
)
def test_local_summary_records_provenance(client, auth_headers, db):
    from app.models.work import Work

    h = auth_headers("editor")
    work = Work(canonical_title="Summarizable", normalized_title="summarizable", abstract="...")
    db.add(work)
    db.commit()
    created = client.post(
        f"/api/v1/works/{work.id}/summaries",
        headers=h,
        json={"summary_type": "extractive"},
    )
    assert created.status_code in (200, 201, 202)
    summaries = client.get(f"/api/v1/works/{work.id}/summaries", headers=h).json()
    assert summaries and summaries[0]["summary_type"]  # provenance fields populated


@pytest.mark.skip(
    reason="M7 topics: ENABLE WHEN BERTopic runs on a shelf scope (SPEC §8.15, off by default)"
)
def test_topic_model_on_shelf_suggests_tags(client, auth_headers, db):
    from app.models.organization import Shelf

    h = auth_headers("editor")
    shelf = Shelf(name="Topical")
    db.add(shelf)
    db.commit()
    r = client.post(
        "/api/v1/ai/topics", headers=h, json={"scope_type": "shelf", "scope_id": str(shelf.id)}
    )
    assert r.status_code in (200, 202)


@pytest.mark.skip(
    reason="M7 semantic search: ENABLE WHEN pgvector embeddings + /search/semantic exist (SPEC §8.15)"
)
def test_semantic_search_returns_neighbours(client, auth_headers):
    h = auth_headers("reader")
    r = client.post("/api/v1/search/semantic", headers=h, json={"q": "attention mechanism"})
    assert r.status_code == 200
    assert isinstance(r.json().get("items"), list)
