"""Citation-summary enrichment tests (Track C: preview, worklist, coverage, missing-list export)."""

from pathlib import Path

import pytest
from app.db.base import Base
from app.models.citation import Reference, ReferenceCitation
from app.models.citation_worklist import MissingWorkDecision
from app.models.organization import Rack, RackShelf, Shelf, ShelfWork
from app.models.source import ImportBatch
from app.models.user import User
from app.models.work import Work
from app.services import citation_summary as cs
from app.services import citation_worklist, external_preview
from app.services.citation_summary import SummaryScope, citation_summary
from app.services.export_service import render_missing_works
from app.services.metadata_enrichment import ExternalMetadata
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.slow


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'enrich.db'}")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            Work.__table__,
            Reference.__table__,
            ReferenceCitation.__table__,
            Shelf.__table__,
            ShelfWork.__table__,
            Rack.__table__,
            RackShelf.__table__,
            ImportBatch.__table__,
            User.__table__,
            MissingWorkDecision.__table__,
        ],
    )
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with session_local() as session:
        cs._SUMMARY_CACHE.clear()
        yield session


def _owner(db) -> User:
    user = User(username="owner", password_hash="x", role="owner")
    db.add(user)
    db.flush()
    return user


def _work(db, title: str, **kwargs) -> Work:
    work = Work(canonical_title=title, normalized_title=title.lower(), **kwargs)
    db.add(work)
    db.flush()
    return work


# --- C3c: coverage metric -----------------------------------------------------------------------


def test_coverage_metric_counts_held_vs_missing(db_session, make_reference) -> None:
    actor = _owner(db_session)
    held = _work(db_session, "Held", doi="10.1/held")
    citing = _work(db_session, "Citing", doi="10.1/citing")
    # One reference resolves to a held local work; one resolves to an external (missing) work.
    make_reference(db_session, citing_work_id=citing.id, doi="10.1/held", title="Held")
    make_reference(db_session, citing_work_id=citing.id, doi="10.9/missing", title="Missing")
    db_session.commit()

    summary = citation_summary(db_session, actor, SummaryScope(type="library"))
    assert summary.coverage_held == 1
    assert summary.coverage_total == 2
    assert summary.coverage_pct == 50.0
    assert held.id  # held work is real; coverage counts it as covered


def test_coverage_none_when_no_resolvable_references(db_session) -> None:
    actor = _owner(db_session)
    _work(db_session, "Solo", doi="10.1/solo")
    db_session.commit()
    summary = citation_summary(db_session, actor, SummaryScope(type="library"))
    assert summary.coverage_total == 0
    assert summary.coverage_pct is None


# --- C1: external-preview service ---------------------------------------------------------------


def test_external_preview_merges_sources(monkeypatch) -> None:
    external_preview._PREVIEW_CACHE.clear()
    preview = external_preview.external_preview(
        doi="10.1/x",
        arxiv_fetcher=lambda *a, **k: None,
        crossref_fetcher=lambda *a, **k: ExternalMetadata(
            source="crossref",
            title="A Title",
            abstract="An abstract.",
            authors=["Ada Lovelace"],
            year=2021,
            venue="Journal",
        ),
        openalex_fetcher=lambda *a, **k: None,
        semantic_scholar_fetcher=lambda *a, **k: None,
    )
    assert preview is not None
    assert preview.title == "A Title"
    assert preview.abstract == "An abstract."
    assert preview.authors == ["Ada Lovelace"]
    assert preview.year == 2021
    assert preview.venue == "Journal"
    assert "crossref" in preview.sources


def test_external_preview_no_identifier_returns_none() -> None:
    external_preview._PREVIEW_CACHE.clear()
    assert external_preview.external_preview() is None


def test_external_preview_graceful_on_all_sources_failing() -> None:
    external_preview._PREVIEW_CACHE.clear()

    def _boom(*a, **k):
        raise RuntimeError("upstream down")

    result = external_preview.external_preview(
        doi="10.1/dead",
        arxiv_fetcher=_boom,
        crossref_fetcher=_boom,
        openalex_fetcher=_boom,
        semantic_scholar_fetcher=_boom,
    )
    assert result is None


# --- C3a: worklist service ----------------------------------------------------------------------


def test_worklist_decision_persists_and_survives_recompute(db_session, make_reference) -> None:
    actor = _owner(db_session)
    p1 = _work(db_session, "P1", doi="10.1/p1")
    make_reference(db_session, citing_work_id=p1.id, doi="10.9/wanted", title="Wanted paper")
    db_session.commit()

    summary = citation_summary(db_session, actor, SummaryScope(type="library"))
    key = summary.frequently_cited_missing[0].key

    citation_worklist.set_decision(db_session, actor.id, key, "ignore")
    db_session.commit()
    assert citation_worklist.list_decisions(db_session, actor.id) == {key: "ignore"}

    # A recompute must not lose the decision (it is keyed by the stable missing key, not cached).
    cs._SUMMARY_CACHE.clear()
    citation_summary(db_session, actor, SummaryScope(type="library"))
    assert citation_worklist.list_decisions(db_session, actor.id) == {key: "ignore"}

    # Upsert flips the decision in place (no duplicate row); clear removes it.
    citation_worklist.set_decision(db_session, actor.id, key, "import")
    db_session.commit()
    assert citation_worklist.list_decisions(db_session, actor.id) == {key: "import"}
    assert citation_worklist.clear_decision(db_session, actor.id, key) is True
    assert citation_worklist.list_decisions(db_session, actor.id) == {}


def test_worklist_rejects_unknown_decision(db_session) -> None:
    actor = _owner(db_session)
    with pytest.raises(ValueError):
        citation_worklist.set_decision(db_session, actor.id, "doi:10.1/x", "maybe")


def test_worklist_bulk_clear_by_decision(db_session) -> None:
    """clear_decisions empties the queue in one call — all, or just one decision value."""
    actor = _owner(db_session)
    citation_worklist.set_decision(db_session, actor.id, "doi:10.1/a", "import")
    citation_worklist.set_decision(db_session, actor.id, "doi:10.1/b", "import")
    citation_worklist.set_decision(db_session, actor.id, "doi:10.1/c", "ignore")
    db_session.commit()

    # Clearing only 'import' empties the queue but keeps the 'ignore' decision.
    removed = citation_worklist.clear_decisions(db_session, actor.id, decision="import")
    db_session.commit()
    assert removed == 2
    assert citation_worklist.list_decisions(db_session, actor.id) == {"doi:10.1/c": "ignore"}

    # Clearing with no filter removes everything that remains.
    assert citation_worklist.clear_decisions(db_session, actor.id) == 1
    db_session.commit()
    assert citation_worklist.list_decisions(db_session, actor.id) == {}


# --- C3b: missing-list export -------------------------------------------------------------------


def test_render_missing_works_bibtex_and_csv() -> None:
    items = [
        cs.MissingWork(
            key="doi:10.9/a",
            title="Attention Is All You Need",
            doi="10.9/a",
            year=2017,
            cited_by_count=5,
            mention_count=7,
            reference_id=None,
            arxiv_id="1706.03762",
        ),
        cs.MissingWork(
            key="title:some paper",
            title="Some Paper",
            doi=None,
            year=None,
            cited_by_count=1,
            mention_count=1,
            reference_id=None,
        ),
    ]
    bibtex = render_missing_works(items, "bibtex")
    assert "@misc{attention2017" in bibtex
    assert "doi = {10.9/a}" in bibtex
    assert "eprint = {1706.03762}" in bibtex
    assert "Cited by 5 paper(s)" in bibtex

    csv_out = render_missing_works(items, "csv")
    lines = csv_out.splitlines()
    assert lines[0] == "key,title,authors,year,doi,arxiv,cited_by_count,mention_count"
    assert "Attention Is All You Need" in lines[1]
    assert lines[1].endswith(",5,7")


def test_render_missing_works_rejects_unknown_format() -> None:
    with pytest.raises(ValueError):
        render_missing_works([], "docx")


# --------------------------------------------------------------------------------------------------
# Endpoint (HTTP/auth) tests — full app against the shared in-memory DB (conftest fixtures).
# --------------------------------------------------------------------------------------------------


def test_external_preview_endpoint_requires_auth(client) -> None:
    assert client.get("/api/v1/citations/external-preview").status_code == 401


def test_external_preview_endpoint_no_identifier(client, auth_headers) -> None:
    response = client.get("/api/v1/citations/external-preview", headers=auth_headers("owner"))
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert "no preview" in body["message"].lower()


def test_external_preview_endpoint_by_reference(
    client, db, auth_headers, monkeypatch, make_reference
) -> None:
    from app.api.v1.endpoints import citations as citations_ep
    from app.services.external_preview import ExternalPreview

    citing = Work(canonical_title="Citing", normalized_title="citing", doi="10.1/citing")
    db.add(citing)
    db.flush()
    ref = make_reference(db, citing_work_id=citing.id, doi="10.9/wanted", title="Wanted")
    db.commit()

    def _stub(*, doi=None, arxiv_id=None, title=None, year=None, settings=None):
        return ExternalPreview(title="Wanted paper", authors=["Grace Hopper"], year=1999, doi=doi)

    monkeypatch.setattr(citations_ep, "external_preview", _stub)
    response = client.get(
        "/api/v1/citations/external-preview",
        params={"reference_id": str(ref.id)},
        headers=auth_headers("owner"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["title"] == "Wanted paper"
    assert body["authors"] == ["Grace Hopper"]


def test_worklist_endpoints_roundtrip(client, auth_headers) -> None:
    headers = auth_headers("owner")
    key = "doi:10.9/wanted"
    put = client.put(
        "/api/v1/citations/worklist", json={"key": key, "decision": "ignore"}, headers=headers
    )
    assert put.status_code == 200
    assert put.json()["decisions"] == {key: "ignore"}

    got = client.get("/api/v1/citations/worklist", headers=headers)
    assert got.json()["decisions"] == {key: "ignore"}

    deleted = client.request(
        "DELETE", "/api/v1/citations/worklist", params={"key": key}, headers=headers
    )
    assert deleted.json()["decisions"] == {}


def test_worklist_bad_decision_400(client, auth_headers) -> None:
    response = client.put(
        "/api/v1/citations/worklist",
        json={"key": "doi:10.9/x", "decision": "later"},
        headers=auth_headers("owner"),
    )
    assert response.status_code == 400


def test_missing_export_endpoint_returns_bibtex(client, db, auth_headers, make_reference) -> None:
    citing = Work(canonical_title="Citing", normalized_title="citing", doi="10.1/citing")
    db.add(citing)
    db.flush()
    make_reference(db, citing_work_id=citing.id, doi="10.9/missing", title="Missing Paper")
    db.commit()

    response = client.get(
        "/api/v1/citations/missing-export",
        params={"format": "bibtex"},
        headers=auth_headers("owner"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["filename"] == "cited-but-missing.bib"
    assert body["content_type"] == "application/x-bibtex"
    assert "Missing Paper" in body["content"]


def test_missing_export_endpoint_rejects_bad_format(client, auth_headers) -> None:
    response = client.get(
        "/api/v1/citations/missing-export",
        params={"format": "docx"},
        headers=auth_headers("owner"),
    )
    assert response.status_code == 400


def test_external_preview_title_fallback_resolves_confident_match(monkeypatch) -> None:
    """No identifier + a title → a confident Crossref title match resolves a DOI and the normal
    multi-source pipeline runs on it (2026-07-17: previews for identifier-less references)."""
    from app.services import web_find

    external_preview._PREVIEW_CACHE.clear()
    monkeypatch.setattr(
        web_find,
        "search_crossref",
        lambda title, authors, year, **kw: [
            web_find.WebCandidate(
                source="crossref",
                title="Attention Is All You Need",
                authors=["Vaswani"],
                year=2017,
                doi="10.5555/attention",
            )
        ],
    )
    preview = external_preview.external_preview(
        title="Attention is all you need",
        year=2017,
        arxiv_fetcher=lambda *a, **k: None,
        crossref_fetcher=lambda doi, **k: ExternalMetadata(
            source="crossref",
            title="Attention Is All You Need",
            abstract="Transformers.",
            authors=["Ashish Vaswani"],
            year=2017,
            venue="NeurIPS",
        ),
        openalex_fetcher=lambda *a, **k: None,
        semantic_scholar_fetcher=lambda *a, **k: None,
    )
    assert preview is not None
    assert preview.title == "Attention Is All You Need"
    assert preview.abstract == "Transformers."


def test_external_preview_title_fallback_rejects_weak_or_wrong_year(monkeypatch) -> None:
    from app.services import web_find

    external_preview._PREVIEW_CACHE.clear()
    # A clearly different paper title → no confident match → None (never a wrong guess).
    monkeypatch.setattr(
        web_find,
        "search_crossref",
        lambda title, authors, year, **kw: [
            web_find.WebCandidate(
                source="crossref",
                title="A Completely Different Paper About Fish",
                authors=[],
                year=2017,
                doi="10.5555/fish",
            )
        ],
    )
    assert external_preview.external_preview(title="Attention is all you need") is None

    # Same title, far-apart year → rejected too (preprint tolerance is ±1).
    monkeypatch.setattr(
        web_find,
        "search_crossref",
        lambda title, authors, year, **kw: [
            web_find.WebCandidate(
                source="crossref",
                title="Attention Is All You Need",
                authors=[],
                year=2010,
                doi="10.5555/wrong-era",
            )
        ],
    )
    assert external_preview.external_preview(title="Attention is all you need", year=2017) is None
