"""External citing papers (batch 10, issue 8): fetch (OpenAlex→S2), store, panel + graph nodes."""

from app.models.external_citation import ExternalCitationLink, ExternalPaper
from app.models.work import Work
from app.services import citing_papers as cp
from sqlalchemy import func, select

OPENALEX_RESOLVE = {"id": "https://openalex.org/W100"}
OPENALEX_CITES = {
    "meta": {"count": 123},
    "results": [
        {
            "id": "https://openalex.org/W200",
            "display_name": "A Citing Paper",
            "publication_year": 2022,
            "doi": "https://doi.org/10.1/citing",
            "authorships": [{"author": {"display_name": "Jane Roe"}}],
            "primary_location": {"source": {"display_name": "Journal of Things"}},
        }
    ],
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
    papers, source, total = cp.fetch_citing_papers(doi="10.1/base")
    assert source == "openalex" and len(papers) == 1
    assert total == 123  # the provider's full count, not the capped list length


def test_fetch_falls_back_to_s2_when_openalex_empty(monkeypatch):
    def fake_get(url, params=None, headers=None):
        if "/citations" in url:  # S2 endpoint
            return _FakeResp(S2_CITES)
        if params and params.get("fields") == "citationCount":  # S2 count follow-up
            return _FakeResp({"citationCount": 7})
        if params and "filter" in params:  # OpenAlex cites page → empty
            return _FakeResp({"results": [], "meta": {"count": 0}})
        return _FakeResp(OPENALEX_RESOLVE)  # OpenAlex resolve

    monkeypatch.setattr(cp, "_get", fake_get)
    papers, source, total = cp.fetch_citing_papers(doi="10.1/base")
    assert source == "semanticscholar" and papers[0].title == "S2 Citing Paper"
    assert total == 7


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
            57,
        ),
    )
    headers = auth_headers("owner")
    resp = client.post(f"/api/v1/works/{work.id}/citing-papers/fetch", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "openalex"
    # The fetch refreshed the cached snapshot from the provider total (was 42).
    assert body["citation_count"] == 57
    assert [i["title"] for i in body["items"]] == ["Cite A"]
    assert body["items"][0]["authors"] == "X Y"
    # Persisted (external paper + link) and returned by the read endpoint.
    assert db.scalar(select(ExternalCitationLink).where(ExternalCitationLink.work_id == work.id))
    assert db.scalar(select(ExternalPaper).where(ExternalPaper.doi == "10.1/a"))
    got = client.get(f"/api/v1/works/{work.id}/citing-papers", headers=headers)
    assert got.status_code == 200 and got.json()["items"][0]["title"] == "Cite A"


def test_store_dedups_shared_citer_across_works(db):
    """A citing paper (same DOI) that cites two works is stored ONCE and linked twice."""
    w1 = _work(db, doi="10.1/one")
    w2 = _work(db, doi="10.1/two")
    citer = cp.CitingPaper(source="openalex", title="Shared Citer", year=2022, doi="10.9/shared")
    cp.store_citing_papers(db, work=w1, papers=[citer], source="openalex")
    cp.store_citing_papers(db, work=w2, papers=[citer], source="openalex")
    db.commit()
    assert db.scalar(select(func.count()).select_from(ExternalPaper)) == 1  # deduped
    assert db.scalar(select(func.count()).select_from(ExternalCitationLink)) == 2  # linked twice


def test_refetch_replaces_links_and_gcs_orphans(db):
    """Refetching a work replaces its links; an external paper with no remaining link is removed."""
    work = _work(db, doi="10.1/base")
    cp.store_citing_papers(
        db,
        work=work,
        papers=[cp.CitingPaper(source="openalex", title="Old", doi="10.9/old")],
        source="openalex",
    )
    db.commit()
    cp.store_citing_papers(
        db,
        work=work,
        papers=[cp.CitingPaper(source="openalex", title="New", doi="10.9/new")],
        source="openalex",
    )
    db.commit()
    keys = set(db.scalars(select(ExternalPaper.dedup_key)).all())
    assert keys == {"doi:10.9/new"}  # the orphaned "old" paper was GC'd
    assert db.scalar(select(func.count()).select_from(ExternalCitationLink)) == 1


def test_fetch_requires_identifier(client, auth_headers, db):
    work = _work(db)  # no DOI / arXiv id
    resp = client.post(
        f"/api/v1/works/{work.id}/citing-papers/fetch", headers=auth_headers("owner")
    )
    assert resp.status_code == 400


def test_reference_graph_includes_citing_nodes(client, auth_headers, db):
    work = _work(db, doi="10.1/base")
    paper = ExternalPaper(
        dedup_key="doi:10.9/x", source="openalex", title="Citing X", year=2022, doi="10.9/x"
    )
    db.add(paper)
    db.flush()
    db.add(ExternalCitationLink(external_paper_id=paper.id, work_id=work.id))
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


# --- local matching of citing papers (incoming direction) ------------------------------------------


def test_store_resolves_in_library_citer(db):
    """A citing paper that IS a library work gets resolved_work_id set (identifier match)."""
    base = _work(db, doi="10.1/base")
    citer_work = Work(
        canonical_title="The Citing Paper",
        normalized_title="the citing paper",
        doi="10.9/citer",
        year=2022,
    )
    db.add(citer_work)
    db.commit()
    cp.store_citing_papers(
        db,
        work=base,
        papers=[cp.CitingPaper(source="openalex", title="The Citing Paper", doi="10.9/citer")],
        source="openalex",
    )
    db.commit()
    stored = db.scalar(select(ExternalPaper).where(ExternalPaper.doi == "10.9/citer"))
    assert stored is not None and stored.resolved_work_id == citer_work.id


def test_store_never_resolves_citer_to_the_cited_work_itself(db):
    """Degenerate self-match guard: a paper cannot cite itself."""
    base = _work(db, doi="10.1/base")
    cp.store_citing_papers(
        db,
        work=base,
        papers=[cp.CitingPaper(source="openalex", title="Base", doi="10.1/base")],
        source="openalex",
    )
    db.commit()
    stored = db.scalar(select(ExternalPaper).where(ExternalPaper.doi == "10.1/base"))
    assert stored is not None and stored.resolved_work_id is None


def test_rescan_links_citers_to_new_work(db):
    """When a work is added later, cached citing papers matching it are linked (reverse rescan)."""
    base = _work(db, doi="10.1/base")
    cp.store_citing_papers(
        db,
        work=base,
        papers=[cp.CitingPaper(source="openalex", title="Late Arrival", doi="10.9/late")],
        source="openalex",
    )
    db.commit()
    stored = db.scalar(select(ExternalPaper).where(ExternalPaper.doi == "10.9/late"))
    assert stored.resolved_work_id is None
    new_work = Work(
        canonical_title="Late Arrival", normalized_title="late arrival", doi="10.9/late"
    )
    db.add(new_work)
    db.flush()
    changed = cp.rescan_external_papers_for_new_work(db, new_work)
    db.commit()
    assert changed == 1
    db.refresh(stored)
    assert stored.resolved_work_id == new_work.id


def test_store_updates_citation_count_snapshot(db):
    work = _work(db, doi="10.1/base", citation_count=1, citation_count_source="crossref")
    cp.store_citing_papers(
        db,
        work=work,
        papers=[cp.CitingPaper(source="openalex", title="C", doi="10.9/c")],
        source="openalex",
        total=250,
    )
    db.commit()
    assert work.citation_count == 250
    assert work.citation_count_source == "openalex"
    assert work.citation_count_fetched_at is not None


def test_delete_work_gcs_orphaned_external_papers(client, auth_headers, db):
    """Deleting a paper prunes external citers that only cited it; shared citers survive."""
    w1 = _work(db, doi="10.1/one")
    w2 = _work(db, doi="10.1/two")
    only = cp.CitingPaper(source="openalex", title="Only Citer", doi="10.9/only")
    shared = cp.CitingPaper(source="openalex", title="Shared Citer", doi="10.9/shared")
    cp.store_citing_papers(db, work=w1, papers=[only, shared], source="openalex")
    cp.store_citing_papers(db, work=w2, papers=[shared], source="openalex")
    db.commit()
    resp = client.delete(f"/api/v1/works/{w1.id}", headers=auth_headers("owner"))
    assert resp.status_code == 204
    keys = set(db.scalars(select(ExternalPaper.dedup_key)).all())
    assert keys == {"doi:10.9/shared"}  # the only-citer was GC'd with its work


def test_merge_moves_external_citations_and_unmerge_restores(db):
    """Merging works repoints incoming external citations to the base; unmerge restores them."""
    from app.core.security import hash_password
    from app.models.user import User
    from app.services.duplicate_resolution import merge_works, unmerge_work

    actor = User(username="merger", password_hash=hash_password("pw"), role="owner")
    db.add(actor)
    db.flush()
    base = _work(db, doi="10.1/base")
    source = _work(db, doi="10.1/source")
    shared = cp.CitingPaper(source="openalex", title="Shared Citer", doi="10.9/shared")
    only = cp.CitingPaper(source="openalex", title="Only Citer", doi="10.9/only")
    cp.store_citing_papers(db, work=base, papers=[shared], source="openalex")
    cp.store_citing_papers(db, work=source, papers=[shared, only], source="openalex")
    db.commit()

    merge_works(db, base=base, source=source, actor=actor)
    db.commit()
    base_paper_ids = set(
        db.scalars(
            select(ExternalCitationLink.external_paper_id).where(
                ExternalCitationLink.work_id == base.id
            )
        ).all()
    )
    assert len(base_paper_ids) == 2  # shared (deduped) + only, all pointing at the base
    assert not db.scalar(
        select(ExternalCitationLink).where(ExternalCitationLink.work_id == source.id)
    )

    unmerge_work(db, base_id=base.id, actor=actor)
    db.commit()
    source_paper_ids = set(
        db.scalars(
            select(ExternalCitationLink.external_paper_id).where(
                ExternalCitationLink.work_id == source.id
            )
        ).all()
    )
    assert len(source_paper_ids) == 2  # both links restored to the shadow
