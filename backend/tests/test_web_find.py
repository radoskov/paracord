"""Find-on-web service tests (#5): adapters, ranking, dedup, isolation, host guards.

All HTTP is mocked — the module-level ``_get`` is monkeypatched to return fixture payloads, so
no test touches the real network. Download tests inject a fake streamer.
"""

import json
from pathlib import Path

import pytest
from app.core.config import Settings
from app.db.base import Base
from app.models.audit import AuditEvent
from app.models.file import File, FileWorkLink, Location
from app.models.metadata import MetadataAssertion
from app.models.work import Work
from app.services import web_find
from app.services.web_find import (
    DownloadRefused,
    WebCandidate,
    _is_denied_host,
    deduplicate,
    download_and_attach,
    find_candidates,
    rank,
    score_candidate,
    search_arxiv,
    search_crossref,
    search_openalex,
    search_semantic_scholar,
    search_unpaywall,
)
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

FIXTURES = Path(__file__).parent / "fixtures"


class _FakeResponse:
    def __init__(self, *, json_data=None, text="", status_code=200):
        self._json = json_data
        self.text = text
        self.status_code = status_code

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")


def _patch_get(monkeypatch, response):
    def fake_get(url, *, params=None, headers=None):
        return response

    monkeypatch.setattr(web_find, "_get", fake_get)


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'webfind.db'}")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            Work.__table__,
            MetadataAssertion.__table__,
            AuditEvent.__table__,
            File.__table__,
            FileWorkLink.__table__,
            Location.__table__,
        ],
    )
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with session_local() as session:
        yield session


# --- per-source adapters ----------------------------------------------------


def test_search_crossref_parses_items(monkeypatch):
    payload = json.loads((FIXTURES / "crossref_search.json").read_text())
    _patch_get(monkeypatch, _FakeResponse(json_data=payload))
    out = search_crossref("Deep Residual Learning", ["He"], 2016)
    assert len(out) == 2
    first = out[0]
    assert first.source == "crossref"
    assert first.doi == "10.1109/cvpr.2016.90"
    assert first.landing_url == "https://doi.org/10.1109/CVPR.2016.90"
    assert "Kaiming He" in first.authors


def test_search_openalex_surfaces_oa_pdf(monkeypatch):
    payload = json.loads((FIXTURES / "openalex_search.json").read_text())
    _patch_get(monkeypatch, _FakeResponse(json_data=payload))
    out = search_openalex("Deep Residual Learning", [], 2016)
    assert len(out) == 1
    assert out[0].is_oa is True
    assert out[0].pdf_url == "https://arxiv.org/pdf/1512.03385"
    assert out[0].doi == "10.1109/cvpr.2016.90"


def test_search_arxiv_builds_pdf_url(monkeypatch):
    xml = (FIXTURES / "arxiv_search.xml").read_text()
    _patch_get(monkeypatch, _FakeResponse(text=xml))
    out = search_arxiv("Deep Residual Learning", [], 2015)
    assert len(out) == 1
    assert out[0].pdf_url == "https://arxiv.org/pdf/1512.03385v1"
    assert out[0].is_oa is True
    assert out[0].year == 2015


def test_search_semantic_scholar_surfaces_oa_pdf(monkeypatch):
    payload = json.loads((FIXTURES / "semantic_scholar_search.json").read_text())
    _patch_get(monkeypatch, _FakeResponse(json_data=payload))
    out = search_semantic_scholar("Deep Residual Learning", [], 2016)
    assert len(out) == 1
    assert out[0].pdf_url.endswith(".pdf")
    assert out[0].is_oa is True


def test_search_unpaywall_returns_oa_links(monkeypatch):
    payload = json.loads((FIXTURES / "unpaywall.json").read_text())
    _patch_get(monkeypatch, _FakeResponse(json_data=payload))
    out = search_unpaywall("10.1109/cvpr.2016.90", email="a@b.org")
    assert out["pdf_url"] == "https://arxiv.org/pdf/1512.03385"
    assert out["is_oa"] is True


def test_search_unpaywall_without_email_is_noop():
    assert search_unpaywall("10.1/x", email=None) is None


def test_adapter_failure_returns_empty_not_raises(monkeypatch):
    def boom(url, *, params=None, headers=None):
        raise RuntimeError("network down")

    monkeypatch.setattr(web_find, "_get", boom)
    assert search_crossref("t", [], 2016) == []
    assert search_openalex("t", [], 2016) == []
    assert search_arxiv("t", [], 2016) == []
    assert search_semantic_scholar("t", [], 2016) == []


# --- dedup + ranking --------------------------------------------------------


def test_deduplicate_merges_same_doi_prefers_oa():
    a = WebCandidate(source="crossref", title="X", doi="10.1/x", year=2020)
    b = WebCandidate(
        source="openalex", title="X", doi="10.1/x", year=2020, pdf_url="http://h/x.pdf", is_oa=True
    )
    merged = deduplicate([a, b])
    assert len(merged) == 1
    assert merged[0].pdf_url == "http://h/x.pdf"
    assert merged[0].is_oa is True
    assert set(merged[0].sources) == {"crossref", "openalex"}


def test_deduplicate_groups_by_title_year_without_doi():
    a = WebCandidate(source="crossref", title="Same Title", year=2020)
    b = WebCandidate(source="arxiv", title="same title", year=2020, pdf_url="http://h/y.pdf")
    assert len(deduplicate([a, b])) == 1


def test_ranking_orders_by_relevance():
    good = WebCandidate(source="crossref", title="Deep Residual Learning", year=2016)
    bad = WebCandidate(source="crossref", title="Birds of the World", year=1999)
    ranked = rank(
        [bad, good],
        query_title="Deep Residual Learning",
        query_year=2016,
        query_authors=[],
        max_candidates=10,
    )
    assert ranked[0].title == "Deep Residual Learning"
    assert ranked[0].score > ranked[1].score


def test_score_oa_bonus_and_author_overlap():
    # A partial title match leaves headroom below the 1.0 cap so the OA bonus is observable.
    base = WebCandidate(source="crossref", title="A Paper About Things", year=2019)
    oa = WebCandidate(source="openalex", title="A Paper About Things", year=2019, is_oa=True)
    s_base = score_candidate(base, query_title="A Paper", query_year=2020, query_authors=[])
    s_oa = score_candidate(oa, query_title="A Paper", query_year=2020, query_authors=[])
    assert s_oa > s_base


def test_rank_caps_max_candidates():
    cands = [WebCandidate(source="crossref", title=f"T{i}", year=2000 + i) for i in range(20)]
    ranked = rank(cands, query_title="T", query_year=2000, query_authors=[], max_candidates=5)
    assert len(ranked) == 5


# --- host guards ------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://sci-hub.se/10.1/x",
        "https://sci-hub.ru/x",
        "http://libgen.is/x.pdf",
        "https://library.lol/main/x",
        "https://z-lib.org/x",
        "https://annas-archive.org/x",
        "https://www.sci-hub.st/x",
    ],
)
def test_denied_hosts_refused(url):
    assert _is_denied_host(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://arxiv.org/pdf/1512.03385",
        "https://api.openalex.org/works",
        "https://doi.org/10.1/x",
        "https://www.cv-foundation.org/x.pdf",
    ],
)
def test_legit_hosts_allowed(url):
    assert _is_denied_host(url) is False


def test_stream_pdf_refuses_denied_host():
    with pytest.raises(DownloadRefused):
        web_find._stream_pdf("https://sci-hub.se/x.pdf", timeout=5, max_bytes=1000)


# --- orchestrator -----------------------------------------------------------


def _seed_work(db):
    work = Work(canonical_title="Deep Residual Learning for Image Recognition", year=2016)
    db.add(work)
    db.flush()
    db.add(
        MetadataAssertion(
            entity_type="work",
            entity_id=work.id,
            field_name="authors",
            value="Kaiming He; Xiangyu Zhang",
            source="grobid",
            selected_as_canonical=True,
        )
    )
    db.commit()
    return work


def test_find_candidates_ranks_injected_sources(db_session):
    work = _seed_work(db_session)
    fetchers = {
        "crossref": lambda: [
            WebCandidate(
                source="crossref",
                title="Deep Residual Learning for Image Recognition",
                doi="10.1/x",
                year=2016,
            )
        ],
        "openalex": lambda: [
            WebCandidate(
                source="openalex",
                title="Deep Residual Learning for Image Recognition",
                doi="10.1/x",
                year=2016,
                pdf_url="https://arxiv.org/pdf/1512.03385",
                is_oa=True,
            )
        ],
        "arxiv": lambda: [],
        "semanticscholar": lambda: [],
    }
    result = find_candidates(db_session, work, settings=Settings(), fetchers=fetchers)
    assert len(result["candidates"]) == 1  # the two were deduped by DOI
    assert result["candidates"][0].is_oa is True
    assert result["degraded_sources"] == []


def test_find_candidates_isolates_failing_source(db_session):
    work = _seed_work(db_session)

    def boom():
        raise RuntimeError("crossref is down")

    fetchers = {
        "crossref": boom,
        "openalex": lambda: [
            WebCandidate(source="openalex", title="Deep Residual Learning", year=2016)
        ],
        "arxiv": lambda: [],
        "semanticscholar": lambda: [],
    }
    result = find_candidates(db_session, work, settings=Settings(), fetchers=fetchers)
    assert "crossref" in result["degraded_sources"]
    assert len(result["candidates"]) == 1  # openalex still returned


def test_find_candidates_empty_title(db_session):
    work = Work(canonical_title="", year=2016)
    db_session.add(work)
    db_session.commit()
    result = find_candidates(db_session, work, settings=Settings())
    assert result["candidates"] == []


# --- download + attach ------------------------------------------------------

_PDF_BYTES = b"%PDF-1.4\n% web-find test fixture\n%%EOF\n"


def _attach_settings(tmp_path):
    return Settings(managed_library_root=str(tmp_path / "lib"))


class _Actor:
    id = None


def test_download_and_attach_success(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(
        web_find, "attach_uploaded_pdf_to_work", web_find.attach_uploaded_pdf_to_work
    )
    work = _seed_work(db_session)

    def fake_stream(url, *, timeout, max_bytes):
        return _PDF_BYTES

    out = download_and_attach(
        db_session,
        work=work,
        candidate_url="https://arxiv.org/pdf/1512.03385.pdf",
        source="openalex",
        actor=_Actor(),
        settings=_attach_settings(tmp_path),
        allowed_urls={"https://arxiv.org/pdf/1512.03385.pdf"},
        streamer=fake_stream,
    )
    assert out["status"] == "attached"
    assert db_session.scalar(select(File)) is not None


def test_download_dedup_returns_deduped(db_session, tmp_path):
    work = _seed_work(db_session)
    settings = _attach_settings(tmp_path)

    def fake_stream(url, *, timeout, max_bytes):
        return _PDF_BYTES

    first = download_and_attach(
        db_session,
        work=work,
        candidate_url="https://h/x.pdf",
        source="arxiv",
        actor=_Actor(),
        settings=settings,
        allowed_urls=None,
        streamer=fake_stream,
    )
    assert first["status"] == "attached"
    second = download_and_attach(
        db_session,
        work=work,
        candidate_url="https://h/x.pdf",
        source="arxiv",
        actor=_Actor(),
        settings=settings,
        allowed_urls=None,
        streamer=fake_stream,
    )
    assert second["status"] == "deduped"


def test_download_non_pdf_manual_upload_no_file(db_session, tmp_path):
    work = _seed_work(db_session)

    def html_stream(url, *, timeout, max_bytes):
        return None  # mimics HTML/login wall

    out = download_and_attach(
        db_session,
        work=work,
        candidate_url="https://h/login.html",
        source="crossref",
        actor=_Actor(),
        settings=_attach_settings(tmp_path),
        allowed_urls=None,
        streamer=html_stream,
    )
    assert out["status"] == "manual_upload_needed"
    assert db_session.scalar(select(File)) is None


def test_download_oversized_errors_no_file(db_session, tmp_path):
    work = _seed_work(db_session)

    def big_stream(url, *, timeout, max_bytes):
        raise ValueError("download exceeds max size cap")

    out = download_and_attach(
        db_session,
        work=work,
        candidate_url="https://h/big.pdf",
        source="crossref",
        actor=_Actor(),
        settings=_attach_settings(tmp_path),
        allowed_urls=None,
        streamer=big_stream,
    )
    assert out["status"] == "error"
    assert db_session.scalar(select(File)) is None


def test_download_url_not_surfaced_refused(db_session, tmp_path):
    work = _seed_work(db_session)
    out = download_and_attach(
        db_session,
        work=work,
        candidate_url="https://evil.example/x.pdf",
        source="crossref",
        actor=_Actor(),
        settings=_attach_settings(tmp_path),
        allowed_urls={"https://arxiv.org/pdf/1.pdf"},
        streamer=lambda *a, **k: _PDF_BYTES,
    )
    assert out["status"] == "error"
    assert db_session.scalar(select(File)) is None


def test_download_denied_host_refused(db_session, tmp_path):
    work = _seed_work(db_session)
    out = download_and_attach(
        db_session,
        work=work,
        candidate_url="https://sci-hub.se/x.pdf",
        source="crossref",
        actor=_Actor(),
        settings=_attach_settings(tmp_path),
        allowed_urls=None,
        streamer=lambda *a, **k: _PDF_BYTES,
    )
    assert out["status"] == "error"
    assert "shadow" in out["reason"].lower()
