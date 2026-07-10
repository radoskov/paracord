"""External citing papers (batch 10, issue 8): fetch (OpenAlex→S2), store, panel + graph nodes."""


from app.models.external_citation import ExternalCitation
from app.models.work import Work
from app.services import citing_papers as cp
from sqlalchemy import select

OPENALEX_RESOLVE = {"id": "https://openalex.org/W100"}
OPENALEX_CITES = {
    "results": [
        {
            "id": "https://openalex.org/W200",
            "display_name": "A Citing Paper",
            "publication_year": 2022,
            "doi": "https://doi.org/10.1/citing",
            "authorships": [{"author": {"display_name": "Jane Roe"}}],
            "primary_location": {"source": {"display_name": "Journal of Things"}},
        }
    ]
}
S2_CITES = {
    "data": [
        {
            "citingPaper": {
                "paperId": "s2abc",
                "title": "S2 Citing Paper",
                "year": 2023,
                "authors": [{"name": "John Doe"}],
                "externalIds": {"DOI": "10.2/s2citing"},
                "venue": "S2 Venue",
            }
        }
    ]
}


class _FakeResp:
    def __init__(self, data: dict, status: int = 200):
        self._data = data
        self.status_code = status

    def json(self) -> dict:
        return self._data

    def raise_for_status(self) -> None:
        pass


def test_parse_openalex_citing():
    papers = cp.parse_openalex_citing(OPENALEX_CITES)
    assert len(papers) == 1
    p = papers[0]
    assert p.title == "A Citing Paper" and p.year == 2022
    assert p.doi == "10.1/citing" and p.venue == "Journal of Things"
    assert p.authors == ["Jane Roe"] and p.external_id == "W200"


def test_parse_s2_citing():
    papers = cp.parse_s2_citing(S2_CITES)
    assert len(papers) == 1 and papers[0].source == "semanticscholar"
    assert papers[0].doi == "10.2/s2citing" and papers[0].venue == "S2 Venue"


def test_fetch_prefers_openalex(monkeypatch):
    def fake_get(url, params=None, headers=None):
        if params and "filter" in params:
            return _FakeResp(OPENALEX_CITES)
        return _FakeResp(OPENALEX_RESOLVE)

    monkeypatch.setattr(cp, "_get", fake_get)
    papers, source = cp.fetch_citing_papers(doi="10.1/base")
    assert source == "openalex" and len(papers) == 1


def test_fetch_falls_back_to_s2_when_openalex_empty(monkeypatch):
    def fake_get(url, params=None, headers=None):
        if "/citations" in url:  # S2 endpoint
            return _FakeResp(S2_CITES)
        if params and "filter" in params:  # OpenAlex cites page → empty
            return _FakeResp({"results": []})
        return _FakeResp(OPENALEX_RESOLVE)  # OpenAlex resolve

    monkeypatch.setattr(cp, "_get", fake_get)
    papers, source = cp.fetch_citing_papers(doi="10.1/base")
    assert source == "semanticscholar" and papers[0].title == "S2 Citing Paper"


def _work(db, **kw) -> Work:
    work = Work(canonical_title="Base", normalized_title="base", **kw)
    db.add(work)
    db.commit()
    db.refresh(work)
    return work


def test_fetch_endpoint_stores_and_panel_returns(client, auth_headers, db, monkeypatch):
    work = _work(db, doi="10.1/base", citation_count=42, citation_count_source="openalex")
    monkeypatch.setattr(
        cp,
        "fetch_citing_papers",
        lambda **kw: (
            [
                cp.CitingPaper(
                    source="openalex", title="Cite A", year=2022, doi="10.1/a", authors=["X Y"]
                )
            ],
            "openalex",
        ),
    )
    headers = auth_headers("owner")
    resp = client.post(f"/api/v1/works/{work.id}/citing-papers/fetch", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "openalex"
    assert body["citation_count"] == 42
    assert [i["title"] for i in body["items"]] == ["Cite A"]
    assert body["items"][0]["authors"] == "X Y"
    # Persisted + returned by the read endpoint.
    assert db.scalar(select(ExternalCitation).where(ExternalCitation.work_id == work.id))
    got = client.get(f"/api/v1/works/{work.id}/citing-papers", headers=headers)
    assert got.status_code == 200 and got.json()["items"][0]["title"] == "Cite A"


def test_fetch_requires_identifier(client, auth_headers, db):
    work = _work(db)  # no DOI / arXiv id
    resp = client.post(
        f"/api/v1/works/{work.id}/citing-papers/fetch", headers=auth_headers("owner")
    )
    assert resp.status_code == 400


def test_reference_graph_includes_citing_nodes(client, auth_headers, db):
    work = _work(db, doi="10.1/base")
    db.add(
        ExternalCitation(
            work_id=work.id, source="openalex", title="Citing X", year=2022, doi="10.9/x"
        )
    )
    db.commit()
    headers = auth_headers("owner")
    without = client.get(f"/api/v1/works/{work.id}/reference-graph", headers=headers).json()
    assert not any(n["kind"] == "citing" for n in without["nodes"])
    with_citing = client.get(
        f"/api/v1/works/{work.id}/reference-graph?include_citing=true", headers=headers
    ).json()
    citing_nodes = [n for n in with_citing["nodes"] if n["kind"] == "citing"]
    assert len(citing_nodes) == 1 and citing_nodes[0]["label"] == "Citing X"
    # Edge points INTO the base paper.
    assert any(
        e["target"] == str(work.id) and e["source"] == citing_nodes[0]["id"]
        for e in with_citing["edges"]
    )
