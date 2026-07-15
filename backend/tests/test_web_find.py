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
from app.models.web_find_allowed_host import WebFindAllowedHost
from app.models.web_find_settings import WebFindSettings
from app.models.work import Work
from app.services import web_find
from app.services.web_find import (
    DownloadRefused,
    WebCandidate,
    _is_allowed_host,
    _is_denied_host,
    deduplicate,
    download_and_attach,
    find_candidates,
    iter_find_candidates,
    rank,
    resolve_final_url,
    score_candidate,
    search_arxiv,
    search_crossref,
    search_openalex,
    search_semantic_scholar,
    search_unpaywall,
)
from app.services.web_find_allowed_hosts import (
    add_allowed_host,
    is_valid_host_pattern,
    list_merged_hosts,
    merged_allowed_hosts,
    remove_allowed_host,
)
from app.services.web_find_settings import set_download_policy
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

# Heavier suite: slow per-test schema setup (full Base.metadata create_all on file-backed SQLite)
# — moved to the full tier. Run via `make test-full`/`make ready-full` or `pytest -m slow`.
pytestmark = pytest.mark.slow

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
            WebFindAllowedHost.__table__,
            WebFindSettings.__table__,
        ],
    )
    # Reset the shared table-presence memo so the policy probe reflects this fresh schema.
    from app.utils.table_presence import clear_cache

    clear_cache()
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


# --- find-on-web v2.1: incremental streaming generator ----------------------


def _no_resolve_settings(**kwargs):
    # Disable redirect resolution so the generator does no network I/O in these tests.
    return Settings(web_find_resolve_enabled=False, **kwargs)


def test_iter_find_candidates_emits_querying_before_done_interleaved(db_session):
    work = _seed_work(db_session)
    fetchers = {
        "crossref": lambda: [WebCandidate(source="crossref", title="X", doi="10.1/a", year=2016)],
        "openalex": lambda: [WebCandidate(source="openalex", title="Y", doi="10.1/b", year=2016)],
        "arxiv": lambda: [],
        "semanticscholar": lambda: [],
    }
    events = list(
        iter_find_candidates(db_session, work, settings=_no_resolve_settings(), fetchers=fetchers)
    )
    # Per source: querying then done, and every querying precedes the final result.
    types = [(e.get("type"), e.get("source"), e.get("status")) for e in events]
    assert types[-1][0] == "result"
    # crossref querying is immediately followed by its done (interleaved, not all-querying-first).
    cr_query = types.index(("source", "crossref", "querying"))
    cr_done = types.index(("source", "crossref", "done"))
    oa_query = types.index(("source", "openalex", "querying"))
    assert cr_query < cr_done < oa_query  # crossref fully done before openalex starts
    # The result is the LAST event; all source events precede it.
    result_idx = next(i for i, t in enumerate(types) if t[0] == "result")
    assert result_idx == len(types) - 1
    assert all(t[0] == "source" for t in types[:result_idx])


def test_iter_find_candidates_querying_precedes_result(db_session):
    work = _seed_work(db_session)
    order: list[str] = []

    def slow_crossref():
        order.append("crossref-ran")
        return [WebCandidate(source="crossref", title="X", doi="10.1/a", year=2016)]

    fetchers = {
        "crossref": slow_crossref,
        "openalex": lambda: [],
        "arxiv": lambda: [],
        "semanticscholar": lambda: [],
    }
    gen = iter_find_candidates(db_session, work, settings=_no_resolve_settings(), fetchers=fetchers)
    first = next(gen)
    # The FIRST event is a querying event, yielded BEFORE the source actually ran.
    assert first == {"type": "source", "source": "crossref", "status": "querying"}
    assert order == []  # not run yet — proves the querying event is emitted pre-fetch
    rest = list(gen)
    assert order == ["crossref-ran"]
    assert rest[-1]["type"] == "result"


def test_find_candidates_wrapper_consumes_generator(db_session):
    work = _seed_work(db_session)
    fetchers = {
        "crossref": lambda: [WebCandidate(source="crossref", title="X", doi="10.1/a", year=2016)],
        "openalex": lambda: [],
        "arxiv": lambda: [],
        "semanticscholar": lambda: [],
    }
    seen: list[dict] = []
    result = find_candidates(
        db_session,
        work,
        settings=_no_resolve_settings(),
        fetchers=fetchers,
        on_progress=seen.append,
    )
    assert len(result["candidates"]) == 1
    assert result["queried_sources"] == ["crossref", "openalex", "arxiv", "semanticscholar"]
    # on_progress saw the per-source events but NOT the final result event.
    assert all(e["type"] == "source" for e in seen)
    assert any(e["status"] == "querying" for e in seen)


# --- find-on-web v2.1: adapters always set a landing_url --------------------


def test_openalex_landing_url_falls_back_to_work_id(monkeypatch):
    # An OpenAlex work with no OA location, no primary landing page, and no DOI still gets a
    # landing_url from the OpenAlex work id (so the UI can always offer "View").
    payload = {
        "results": [
            {
                "id": "https://openalex.org/W123",
                "display_name": "The Ontolingua Server",
                "publication_year": 1996,
                "doi": None,
                "open_access": {"is_oa": False},
                "best_oa_location": None,
                "primary_location": None,
                "authorships": [],
            }
        ]
    }
    _patch_get(monkeypatch, _FakeResponse(json_data=payload))
    out = search_openalex("The Ontolingua Server", [], 1996)
    assert len(out) == 1
    assert out[0].pdf_url is None
    assert out[0].landing_url == "https://openalex.org/W123"


def test_semantic_scholar_landing_url_falls_back_to_paper_url(monkeypatch):
    payload = {
        "data": [
            {
                "paperId": "abc123",
                "title": "A Paper",
                "year": 2020,
                "externalIds": {},
                "openAccessPdf": None,
                "isOpenAccess": False,
                "url": "https://www.semanticscholar.org/paper/abc123",
                "authors": [],
            }
        ]
    }
    _patch_get(monkeypatch, _FakeResponse(json_data=payload))
    out = search_semantic_scholar("A Paper", [], 2020)
    assert len(out) == 1
    assert out[0].pdf_url is None
    assert out[0].landing_url == "https://www.semanticscholar.org/paper/abc123"


# --- find-on-web v2.1: resolve_final_url + platform -------------------------


class _ResolveResponse:
    def __init__(self, url, history=None):
        self.url = url
        self.history = history or []


class _ResolveClient:
    """A fake httpx.Client that returns a canned final response from head()."""

    def __init__(self, final_url, history_urls=()):
        self._final_url = final_url
        self._history_urls = history_urls

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def head(self, url):
        history = [_ResolveResponse(u) for u in self._history_urls]
        return _ResolveResponse(self._final_url, history=history)


def test_resolve_final_url_follows_cross_host_redirect(monkeypatch):
    # doi.org → sciencedirect.com (cross-host); a public resolver keeps it non-internal.
    client = _ResolveClient(
        "https://www.sciencedirect.com/science/article/pii/X",
        history_urls=["https://doi.org/10.1/x"],
    )
    monkeypatch.setattr(web_find.httpx, "Client", lambda **kw: client)
    out = resolve_final_url(
        "https://doi.org/10.1/x", timeout=4.0, resolver=lambda h: ["93.184.216.34"]
    )
    assert out == "https://www.sciencedirect.com/science/article/pii/X"


def test_resolve_final_url_blocked_on_denylisted_hop(monkeypatch):
    client = _ResolveClient("https://sci-hub.se/10.1/x", history_urls=["https://doi.org/10.1/x"])
    monkeypatch.setattr(web_find.httpx, "Client", lambda **kw: client)
    out = resolve_final_url(
        "https://doi.org/10.1/x", timeout=4.0, resolver=lambda h: ["93.184.216.34"]
    )
    assert out is None  # a denylisted hop stops resolution


def test_resolve_final_url_blocked_on_private_ip(monkeypatch):
    # The very first host resolves to a private IP → refused before any request.
    out = resolve_final_url(
        "https://internal.example/x", timeout=4.0, resolver=lambda h: ["10.0.0.5"]
    )
    assert out is None


def test_resolve_final_url_degrades_to_none_on_timeout(monkeypatch):
    class _Boom:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def head(self, url):
            raise web_find.httpx.TimeoutException("timeout")

    monkeypatch.setattr(web_find.httpx, "Client", lambda **kw: _Boom())
    out = resolve_final_url(
        "https://doi.org/10.1/x", timeout=4.0, resolver=lambda h: ["93.184.216.34"]
    )
    assert out is None


def test_resolve_final_url_returns_final_host_after_multi_hop(monkeypatch):
    # A DOI bounces linkhub.elsevier.com → www.sciencedirect.com (item 4): the resolved URL is the
    # TRUE final host (sciencedirect.com), NOT the first elsevier hop and not a domain collapse.
    client = _ResolveClient(
        "https://www.sciencedirect.com/science/article/pii/X",
        history_urls=[
            "https://doi.org/10.1016/x",
            "https://linkinghub.elsevier.com/retrieve/pii/X",
        ],
    )
    monkeypatch.setattr(web_find.httpx, "Client", lambda **kw: client)
    out = resolve_final_url(
        "https://doi.org/10.1016/x", timeout=4.0, resolver=lambda h: ["93.184.216.34"]
    )
    assert out == "https://www.sciencedirect.com/science/article/pii/X"
    assert web_find._host(out) == "www.sciencedirect.com"


def test_find_candidates_populates_platform_from_resolution(db_session, monkeypatch):
    work = _seed_work(db_session)
    client = _ResolveClient("https://www.sciencedirect.com/science/article/pii/X")
    monkeypatch.setattr(web_find.httpx, "Client", lambda **kw: client)
    fetchers = {
        "crossref": lambda: [
            WebCandidate(
                source="crossref",
                title="Deep Residual Learning for Image Recognition",
                doi="10.1/x",
                year=2016,
                landing_url="https://doi.org/10.1/x",
            )
        ],
        "openalex": lambda: [],
        "arxiv": lambda: [],
        "semanticscholar": lambda: [],
    }
    result = find_candidates(
        db_session,
        work,
        settings=Settings(web_find_resolve_enabled=True),
        fetchers=fetchers,
        resolver=lambda h: ["93.184.216.34"],
    )
    cand = result["candidates"][0]
    assert cand.resolved_url == "https://www.sciencedirect.com/science/article/pii/X"
    assert cand.platform == "www.sciencedirect.com"


def test_resolve_disabled_sets_platform_from_landing_host(db_session):
    work = _seed_work(db_session)
    fetchers = {
        "crossref": lambda: [
            WebCandidate(
                source="crossref",
                title="Deep Residual Learning for Image Recognition",
                doi="10.1/x",
                year=2016,
                landing_url="https://doi.org/10.1/x",
            )
        ],
        "openalex": lambda: [],
        "arxiv": lambda: [],
        "semanticscholar": lambda: [],
    }
    result = find_candidates(db_session, work, settings=_no_resolve_settings(), fetchers=fetchers)
    cand = result["candidates"][0]
    assert cand.resolved_url is None
    assert cand.platform == "doi.org"


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
        streamer=fake_stream,
    )
    assert out["status"] == "attached"
    assert db_session.scalar(select(File)) is not None


def test_download_backfills_arxiv_id_and_doi(db_session, tmp_path):
    """P3: the candidate's identifiers land on a work whose fields were empty."""
    work = Work(canonical_title="Attention Is All You Need")
    db_session.add(work)
    db_session.flush()

    def fake_stream(url, *, timeout, max_bytes):
        return _PDF_BYTES

    out = download_and_attach(
        db_session,
        work=work,
        candidate_url="https://arxiv.org/pdf/1706.03762.pdf",
        source="arxiv",
        actor=_Actor(),
        settings=_attach_settings(tmp_path),
        doi="10.5555/attention",
        arxiv_id="1706.03762v5",
        streamer=fake_stream,
    )
    assert out["status"] == "attached"
    db_session.refresh(work)
    assert work.arxiv_id == "1706.03762v5"
    assert work.arxiv_base_id == "1706.03762"
    assert work.doi == "10.5555/attention"


def test_download_respects_locked_identifier(db_session, tmp_path):
    """P3: a user-locked identifier is never overwritten; an empty one is still filled."""
    work = Work(canonical_title="Locked paper", doi="10.1/kept")
    work.confirmed_fields = ["doi"]
    db_session.add(work)
    db_session.flush()

    def fake_stream(url, *, timeout, max_bytes):
        return _PDF_BYTES

    download_and_attach(
        db_session,
        work=work,
        candidate_url="https://arxiv.org/pdf/2101.00001.pdf",
        source="arxiv",
        actor=_Actor(),
        settings=_attach_settings(tmp_path),
        doi="10.2/other",
        arxiv_id="2101.00001",
        streamer=fake_stream,
    )
    db_session.refresh(work)
    assert work.doi == "10.1/kept"  # locked → not overwritten
    assert work.arxiv_id == "2101.00001"  # empty + unlocked → filled


def test_download_dedup_returns_deduped(db_session, tmp_path):
    work = _seed_work(db_session)
    settings = _attach_settings(tmp_path)

    def fake_stream(url, *, timeout, max_bytes):
        return _PDF_BYTES

    first = download_and_attach(
        db_session,
        work=work,
        candidate_url="https://arxiv.org/pdf/x.pdf",
        source="arxiv",
        actor=_Actor(),
        settings=settings,
        streamer=fake_stream,
    )
    assert first["status"] == "attached"
    second = download_and_attach(
        db_session,
        work=work,
        candidate_url="https://arxiv.org/pdf/x.pdf",
        source="arxiv",
        actor=_Actor(),
        settings=settings,
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
        candidate_url="https://europepmc.org/login.html",
        source="crossref",
        actor=_Actor(),
        settings=_attach_settings(tmp_path),
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
        candidate_url="https://zenodo.org/big.pdf",
        source="crossref",
        actor=_Actor(),
        settings=_attach_settings(tmp_path),
        streamer=big_stream,
    )
    assert out["status"] == "error"
    assert db_session.scalar(select(File)) is None


def test_download_unknown_host_restricted_errors(db_session, tmp_path):
    """Default (restricted) mode refuses a host that is not on the merged allow-list."""
    work = _seed_work(db_session)
    out = download_and_attach(
        db_session,
        work=work,
        candidate_url="https://evil.example/x.pdf",
        source="crossref",
        actor=_Actor(),
        settings=_attach_settings(tmp_path),
        streamer=lambda *a, **k: _PDF_BYTES,
    )
    assert out["status"] == "error"
    assert db_session.scalar(select(File)) is None


def test_download_denied_host_blocked(db_session, tmp_path):
    """A shadow-library host is a HARD BLOCK in every mode (stores nothing)."""
    work = _seed_work(db_session)
    out = download_and_attach(
        db_session,
        work=work,
        candidate_url="https://sci-hub.se/x.pdf",
        source="crossref",
        actor=_Actor(),
        settings=_attach_settings(tmp_path),
        streamer=lambda *a, **k: _PDF_BYTES,
    )
    assert out["status"] == "blocked"
    assert "shadow" in out["reason"].lower()


# --- find-on-web item 4: attempt the resolved/landing URL (no direct pdf_url) ---
#
# A candidate with only a resolved_url / landing_url is ATTEMPTED (the caller hands that URL in as
# candidate_url): it attaches when the fetch yields a real %PDF, and falls back to
# manual_upload_needed for an HTML/login/paywall page — and ALL security gates still apply to the
# attempted URL.


def test_download_landing_url_without_pdf_attaches_when_pdf(db_session, tmp_path):
    """A landing/resolved URL (no direct pdf_url) that serves a real PDF is attached."""
    work = _seed_work(db_session)

    def fake_stream(url, *, timeout, max_bytes):
        return _PDF_BYTES  # the landing URL happened to serve a real PDF

    out = download_and_attach(
        db_session,
        work=work,
        candidate_url="https://zenodo.org/record/123",  # allow-listed landing URL, no .pdf
        source="openalex",
        actor=_Actor(),
        settings=_attach_settings(tmp_path),
        streamer=fake_stream,
    )
    assert out["status"] == "attached"
    assert db_session.scalar(select(File)) is not None


def test_download_landing_url_html_manual_upload_no_file(db_session, tmp_path):
    """A landing/resolved URL that serves HTML (publisher login/paywall) → manual_upload_needed."""
    work = _seed_work(db_session)

    def html_stream(url, *, timeout, max_bytes):
        return None  # not a real PDF (HTML/login/paywall)

    out = download_and_attach(
        db_session,
        work=work,
        candidate_url="https://europepmc.org/article/landing",
        source="crossref",
        actor=_Actor(),
        settings=_attach_settings(tmp_path),
        streamer=html_stream,
    )
    assert out["status"] == "manual_upload_needed"
    assert db_session.scalar(select(File)) is None


def test_download_attempted_landing_url_denylist_blocked(db_session, tmp_path):
    """The denylist still HARD-BLOCKS an attempted landing/resolved URL (stores nothing)."""
    work = _seed_work(db_session)
    out = download_and_attach(
        db_session,
        work=work,
        candidate_url="https://sci-hub.se/10.1/x",  # attempted (no pdf_url), still blocked
        source="crossref",
        actor=_Actor(),
        settings=_attach_settings(tmp_path),
        streamer=lambda *a, **k: _PDF_BYTES,
    )
    assert out["status"] == "blocked"
    assert db_session.scalar(select(File)) is None


def test_download_attempted_landing_url_mode_gate_errors(db_session, tmp_path):
    """The policy-mode gate still refuses an attempted landing URL on an off-allow-list host."""
    work = _seed_work(db_session)
    out = download_and_attach(
        db_session,
        work=work,
        candidate_url="https://unknown-publisher.example/article",  # not allow-listed
        source="crossref",
        actor=_Actor(),
        settings=_attach_settings(tmp_path),  # default restricted mode
        streamer=lambda *a, **k: _PDF_BYTES,
    )
    assert out["status"] == "error"
    assert db_session.scalar(select(File)) is None


def test_download_attempted_landing_url_internal_ip_blocked(db_session, tmp_path):
    """The SSRF guard still HARD-BLOCKS an attempted landing URL resolving to an internal IP."""
    work = _seed_work(db_session)
    set_download_policy(db_session, policy="unrestricted")
    out = download_and_attach(
        db_session,
        work=work,
        candidate_url="https://internal.example/article",
        source="crossref",
        actor=_Actor(),
        settings=_attach_settings(tmp_path),
        confirmed=True,  # even confirmed, the IP guard wins
        streamer=web_find._stream_pdf,  # real streamer does the per-hop IP guard
        resolver=lambda h: ["10.0.0.7"],
    )
    assert out["status"] == "blocked"
    assert db_session.scalar(select(File)) is None


# --- download-host allowlist (batch 2 #5 hardening) -------------------------


@pytest.mark.parametrize(
    "host,pattern,expected",
    [
        ("arxiv.org", "arxiv.org", True),
        ("export.arxiv.org", "arxiv.org", True),  # parent-domain suffix match
        ("evil-arxiv.org", "arxiv.org", False),  # not a subdomain
        ("notarxiv.org", "arxiv.org", False),
        ("repo.example.org", "*.example.org", True),  # wildcard matches subdomain
        ("example.org", "*.example.org", False),  # wildcard does NOT match the apex
        ("example.org", "example.org", True),
    ],
)
def test_host_matches_suffix_and_wildcard(host, pattern, expected):
    assert web_find._host_matches(host, pattern) is expected


def test_is_allowed_host_against_defaults():
    assert _is_allowed_host("https://arxiv.org/pdf/1.pdf", set(web_find.DEFAULT_ALLOWED_HOSTS))
    assert not _is_allowed_host("https://evil.example/1.pdf", set(web_find.DEFAULT_ALLOWED_HOSTS))


def test_merged_hosts_is_defaults_union_db(db_session):
    add_allowed_host(db_session, host="repo.example.org", created_by_user_id=None)
    db_session.commit()
    merged = merged_allowed_hosts(db_session)
    assert "repo.example.org" in merged
    assert set(web_find.DEFAULT_ALLOWED_HOSTS).issubset(merged)


def test_list_marks_default_locked_vs_db_removable(db_session):
    add_allowed_host(db_session, host="repo.example.org", created_by_user_id=None)
    db_session.commit()
    items = {item["host"]: item for item in list_merged_hosts(db_session)}
    assert items["arxiv.org"]["source"] == "default"
    assert items["arxiv.org"]["removable"] is False
    assert items["arxiv.org"]["id"] is None
    assert items["repo.example.org"]["source"] == "db"
    assert items["repo.example.org"]["removable"] is True
    assert items["repo.example.org"]["id"] is not None


def test_add_host_validates_and_dedupes(db_session):
    with pytest.raises(ValueError, match="plausible hostname"):
        add_allowed_host(db_session, host="not a host!", created_by_user_id=None)
    with pytest.raises(ValueError, match="required"):
        add_allowed_host(db_session, host="   ", created_by_user_id=None)
    # Cannot re-add a built-in default.
    with pytest.raises(ValueError, match="already"):
        add_allowed_host(db_session, host="arxiv.org", created_by_user_id=None)
    # Cannot add the same DB host twice.
    add_allowed_host(db_session, host="repo.example.org", created_by_user_id=None)
    db_session.commit()
    with pytest.raises(ValueError, match="already"):
        add_allowed_host(db_session, host="repo.example.org", created_by_user_id=None)


def test_is_valid_host_pattern():
    assert is_valid_host_pattern("example.org")
    assert is_valid_host_pattern("*.example.org")
    assert is_valid_host_pattern("sub.example.co.uk")
    assert not is_valid_host_pattern("nodot")
    assert not is_valid_host_pattern("bad host.org")
    assert not is_valid_host_pattern("")


def test_remove_db_host_works_default_cannot(db_session):
    row = add_allowed_host(db_session, host="repo.example.org", created_by_user_id=None)
    db_session.commit()
    remove_allowed_host(db_session, host_id=row.id)
    db_session.commit()
    assert db_session.get(WebFindAllowedHost, row.id) is None
    # Defaults have no DB row, so a random id is a "not found" (they can never be removed).
    import uuid as _uuid

    with pytest.raises(ValueError, match="not found"):
        remove_allowed_host(db_session, host_id=_uuid.uuid4())


def test_download_non_allowlisted_host_refused_no_file(db_session, tmp_path):
    work = _seed_work(db_session)
    out = download_and_attach(
        db_session,
        work=work,
        candidate_url="https://random.example/paper.pdf",
        source="crossref",
        actor=_Actor(),
        settings=_attach_settings(tmp_path),
        streamer=lambda *a, **k: _PDF_BYTES,
    )
    assert out["status"] == "error"
    assert "allowed-downloads" in out["reason"].lower()
    assert db_session.scalar(select(File)) is None


def test_download_allowlisted_default_host_attaches(db_session, tmp_path):
    work = _seed_work(db_session)
    out = download_and_attach(
        db_session,
        work=work,
        candidate_url="https://arxiv.org/pdf/1512.03385.pdf",
        source="arxiv",
        actor=_Actor(),
        settings=_attach_settings(tmp_path),
        streamer=lambda *a, **k: _PDF_BYTES,
    )
    assert out["status"] == "attached"
    assert db_session.scalar(select(File)) is not None


def test_download_db_added_host_attaches(db_session, tmp_path):
    work = _seed_work(db_session)
    add_allowed_host(db_session, host="repo.example.org", created_by_user_id=None)
    db_session.commit()
    out = download_and_attach(
        db_session,
        work=work,
        candidate_url="https://repo.example.org/paper.pdf",
        source="crossref",
        actor=_Actor(),
        settings=_attach_settings(tmp_path),
        streamer=lambda *a, **k: _PDF_BYTES,
    )
    assert out["status"] == "attached"
    assert db_session.scalar(select(File)) is not None


def test_denylist_wins_even_if_added_to_allowlist(db_session, tmp_path):
    """A denylisted host is refused even if someone manages to add it to the allowlist."""
    work = _seed_work(db_session)
    # Force the denied host into the DB allowlist (bypassing validation) to prove denylist wins.
    db_session.add(WebFindAllowedHost(host="sci-hub.se", created_by_user_id=None))
    db_session.commit()
    assert "sci-hub.se" in merged_allowed_hosts(db_session)
    out = download_and_attach(
        db_session,
        work=work,
        candidate_url="https://sci-hub.se/x.pdf",
        source="crossref",
        actor=_Actor(),
        settings=_attach_settings(tmp_path),
        streamer=lambda *a, **k: _PDF_BYTES,
    )
    assert out["status"] == "blocked"
    assert "shadow" in out["reason"].lower()
    assert db_session.scalar(select(File)) is None


# --- find-on-web v2: download-policy mode gate + SSRF/IP guard + streaming ----

# A known-publisher host (in KNOWN_PUBLISHER_HOSTS but NOT in the merged allow-list defaults).
_KNOWN_PUBLISHER_URL = "https://ieeexplore.ieee.org/document/123.pdf"
# A host on neither the allow-list nor the known-publisher list.
_UNKNOWN_URL = "https://random.example/paper.pdf"


def _download(db, tmp_path, url, *, confirmed=False):
    return download_and_attach(
        db,
        work=_seed_work(db),
        candidate_url=url,
        source="crossref",
        actor=_Actor(),
        settings=_attach_settings(tmp_path),
        confirmed=confirmed,
        streamer=lambda *a, **k: _PDF_BYTES,
    )


def test_mode_restricted_blocks_non_allowlist_host(db_session, tmp_path):
    set_download_policy(db_session, policy="restricted")
    db_session.commit()
    # A known-publisher host is NOT enough in restricted mode.
    out = _download(db_session, tmp_path, _KNOWN_PUBLISHER_URL)
    assert out["status"] == "error"
    assert "allowed-downloads" in out["reason"].lower()


def test_mode_careful_allows_known_publisher_host(db_session, tmp_path):
    set_download_policy(db_session, policy="careful")
    db_session.commit()
    out = _download(db_session, tmp_path, _KNOWN_PUBLISHER_URL)
    assert out["status"] == "attached"
    # But a totally unknown host is still refused in careful mode.
    out2 = _download(db_session, tmp_path, _UNKNOWN_URL)
    assert out2["status"] == "error"


def test_mode_unrestricted_allowlisted_host_no_confirmation(db_session, tmp_path):
    set_download_policy(db_session, policy="unrestricted")
    db_session.commit()
    # Allow-list/known hosts must NEVER require confirmation, even in unrestricted mode.
    out = _download(db_session, tmp_path, "https://arxiv.org/pdf/x.pdf")
    assert out["status"] == "attached"
    out2 = _download(db_session, tmp_path, _KNOWN_PUBLISHER_URL)
    assert out2["status"] == "attached"


def test_mode_unrestricted_unknown_host_needs_confirmation_then_attaches(db_session, tmp_path):
    set_download_policy(db_session, policy="unrestricted")
    db_session.commit()
    # Unknown public host, not yet confirmed → needs_confirmation, nothing stored.
    out = _download(db_session, tmp_path, _UNKNOWN_URL, confirmed=False)
    assert out["status"] == "needs_confirmation"
    assert out["url"] == _UNKNOWN_URL
    assert db_session.scalar(select(File)) is None
    # Re-sent with confirmed=true → attaches.
    out2 = _download(db_session, tmp_path, _UNKNOWN_URL, confirmed=True)
    assert out2["status"] == "attached"


def test_landing_page_fallback_follows_download_pdf_button(db_session, tmp_path):
    """UX batch 3: an HTML landing page no longer dead-ends — publisher URL rewrites and the
    page's citation_pdf_url / "Download PDF" affordances are tried (still under the policy)."""
    set_download_policy(db_session, policy="careful")
    db_session.commit()
    landing = "https://link.springer.com/article/10.1007/xyz"
    pdf_url = "https://link.springer.com/real/download.pdf"
    tried: list[str] = []

    def fake_stream(url, *, timeout, max_bytes, **_kw):
        tried.append(url)
        return _PDF_BYTES if url == pdf_url else None  # landing + rewrites are HTML → None

    def fake_html_fetcher(url, **_kw):
        return (
            f'<html><a class="c-pdf-download__link" href="{pdf_url}">Download PDF</a></html>',
            url,
        )

    out = download_and_attach(
        db_session,
        work=_seed_work(db_session),
        candidate_url=landing,
        source="crossref",
        actor=_Actor(),
        settings=_attach_settings(tmp_path),
        streamer=fake_stream,
        html_fetcher=fake_html_fetcher,
    )
    assert out["status"] == "attached"
    # The publisher rewrite ran before the page-extracted link; the button link finally hit.
    assert tried[0] == landing
    assert "https://link.springer.com/content/pdf/10.1007/xyz.pdf" in tried
    assert tried[-1] == pdf_url


def test_elsevier_api_fallback_used_for_10_1016_doi_with_key(db_session, tmp_path, monkeypatch):
    """UX batch 3: with an Elsevier key configured, a ScienceDirect (10.1016) DOI is fetched via
    the official Article Retrieval API, key in the request header."""

    set_download_policy(db_session, policy="careful")
    db_session.commit()
    # This fixture's schema has no app_config table — configure via the settings fallback.
    settings_obj = _attach_settings(tmp_path)
    monkeypatch.setattr(settings_obj, "web_find_elsevier_api_key", "sekret-key", raising=False)
    actor = _Actor()
    actor.elsevier_api_allowed = True  # per-user gate (off by default)
    seen: list[tuple[str, dict | None]] = []

    def fake_stream(url, *, timeout, max_bytes, policy=None, merged_allowed=None, resolver=None,
                    headers=None):
        seen.append((url, headers))
        return _PDF_BYTES if "api.elsevier.com" in url else None

    out = download_and_attach(
        db_session,
        work=_seed_work(db_session),
        candidate_url="https://www.sciencedirect.com/science/article/pii/S123",
        source="crossref",
        actor=actor,
        settings=settings_obj,
        doi="10.1016/j.artint.2024.104123",
        streamer=fake_stream,
        html_fetcher=lambda url, **_kw: ("<html></html>", url),
    )
    assert out["status"] == "attached"
    api_calls = [(u, h) for u, h in seen if "api.elsevier.com" in u]
    assert api_calls, seen
    url, headers = api_calls[0]
    assert "10.1016%2Fj.artint.2024.104123" in url and "httpAccept=application%2Fpdf" in url
    assert headers == {"X-ELS-APIKey": "sekret-key"}

    # The per-user gate (off by default) blocks the API path for a not-allowed actor.
    seen.clear()
    out2 = download_and_attach(
        db_session,
        work=_seed_work(db_session),
        candidate_url="https://www.sciencedirect.com/science/article/pii/S124",
        source="crossref",
        actor=_Actor(),  # elsevier_api_allowed not set → False
        settings=settings_obj,
        doi="10.1016/j.artint.2024.104124",
        streamer=fake_stream,
        html_fetcher=lambda url, **_kw: ("<html></html>", url),
    )
    assert out2["status"] == "manual_upload_needed"
    assert not [u for u, _h in seen if "api.elsevier.com" in u]


def test_landing_page_fallback_never_escapes_the_policy(db_session, tmp_path):
    """A page-extracted link on a denied/refused host is skipped, never fetched."""
    set_download_policy(db_session, policy="careful")
    db_session.commit()
    landing = "https://link.springer.com/article/10.1007/xyz"

    def fake_stream(url, *, timeout, max_bytes, policy=None, merged_allowed=None, resolver=None):
        # Mirror the real streamer's per-URL gate: refuse anything not policy-allowed.
        outcome, reason = web_find._classify_download_host(
            url, policy=policy or "careful", merged_allowed=merged_allowed or set()
        )
        if outcome != "allow":
            raise DownloadRefused(reason)
        return None  # allowed hosts return HTML (no PDF) in this scenario

    def fake_html_fetcher(url, **_kw):
        return ('<a href="https://sci-hub.se/x.pdf">Download PDF</a>', url)

    out = download_and_attach(
        db_session,
        work=_seed_work(db_session),
        candidate_url=landing,
        source="crossref",
        actor=_Actor(),
        settings=_attach_settings(tmp_path),
        streamer=fake_stream,
        html_fetcher=fake_html_fetcher,
    )
    assert out["status"] == "manual_upload_needed"
    assert db_session.scalar(select(File)) is None


def test_denied_host_blocked_in_every_mode_incl_unrestricted_confirmed(db_session, tmp_path):
    for policy in ("restricted", "careful", "unrestricted"):
        set_download_policy(db_session, policy=policy)
        db_session.commit()
        out = _download(db_session, tmp_path, "https://sci-hub.se/x.pdf", confirmed=True)
        assert out["status"] == "blocked", policy
        assert "shadow" in out["reason"].lower()
        assert db_session.scalar(select(File)) is None


@pytest.mark.parametrize(
    "ip",
    ["10.0.0.5", "127.0.0.1", "169.254.169.254", "192.168.1.1", "::1", "fc00::1"],
)
def test_internal_ip_host_blocked_in_every_mode(db_session, tmp_path, ip):
    """A host resolving to a private/loopback/link-local IP is a HARD BLOCK in every mode."""

    def fake_resolver(host):
        return [ip]

    for policy in ("restricted", "careful", "unrestricted"):
        set_download_policy(db_session, policy=policy)
        db_session.commit()
        out = download_and_attach(
            db_session,
            work=_seed_work(db_session),
            candidate_url="https://arxiv.org/pdf/x.pdf",  # an allow-listed host name…
            source="crossref",
            actor=_Actor(),
            settings=_attach_settings(tmp_path),
            confirmed=True,
            # …that resolves to an internal IP → SSRF guard blocks it before any fetch.
            resolver=fake_resolver,
            streamer=web_find._stream_pdf,  # real streamer does the per-hop IP guard
        )
        assert out["status"] == "blocked", policy
        assert "internal" in out["reason"].lower() or "private" in out["reason"].lower()
        assert db_session.scalar(select(File)) is None


def test_ip_guard_helpers():
    assert web_find._ip_is_internal("10.0.0.1") is True
    assert web_find._ip_is_internal("127.0.0.1") is True
    assert web_find._ip_is_internal("169.254.0.1") is True
    assert web_find._ip_is_internal("::1") is True
    assert web_find._ip_is_internal("8.8.8.8") is False
    assert web_find._ip_is_internal("not-an-ip") is True  # unparsable → unsafe


def test_known_publisher_host_matching():
    allowed_empty: set[str] = set()
    # careful mode: known publisher allowed.
    out, _ = web_find._classify_download_host(
        _KNOWN_PUBLISHER_URL, policy="careful", merged_allowed=allowed_empty, check_ip=False
    )
    assert out == "allow"
    out2, _ = web_find._classify_download_host(
        _UNKNOWN_URL, policy="careful", merged_allowed=allowed_empty, check_ip=False
    )
    assert out2 == "error"


def test_non_http_scheme_blocked():
    out, reason = web_find._classify_download_host(
        "ftp://arxiv.org/x.pdf", policy="unrestricted", merged_allowed=set(), check_ip=False
    )
    assert out == "hard_block"
    assert "http" in reason.lower()


def test_find_candidates_streaming_progress_events(db_session):
    """find_candidates fires per-source querying/done(failed) progress callbacks in order."""
    work = _seed_work(db_session)

    def boom():
        raise RuntimeError("down")

    fetchers = {
        "crossref": lambda: [
            WebCandidate(source="crossref", title="Deep Residual Learning", year=2016)
        ],
        "openalex": boom,
        "arxiv": lambda: [],
        "semanticscholar": lambda: [],
    }
    events: list[dict] = []
    result = find_candidates(
        db_session,
        work,
        settings=Settings(),
        sources=["crossref", "openalex"],
        fetchers=fetchers,
        on_progress=events.append,
    )
    # crossref: querying then done(count=1); openalex: querying then failed.
    assert events[0] == {"type": "source", "source": "crossref", "status": "querying"}
    assert events[1] == {"type": "source", "source": "crossref", "status": "done", "count": 1}
    assert events[2] == {"type": "source", "source": "openalex", "status": "querying"}
    assert events[3] == {"type": "source", "source": "openalex", "status": "failed"}
    assert "openalex" in result["degraded_sources"]
