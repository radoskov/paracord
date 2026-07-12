"""External metadata enrichment tests (arXiv / Crossref)."""

import json
from pathlib import Path

import pytest
from app.core.config import Settings
from app.db.base import Base
from app.models.app_config import AppConfig
from app.models.audit import AuditEvent
from app.models.citation import Reference
from app.models.external_citation import ExternalCitationLink, ExternalPaper
from app.models.metadata import MetadataAssertion
from app.models.work import Work
from app.services.metadata_enrichment import (
    enrich_work,
    parse_arxiv_atom,
    parse_crossref,
    parse_openalex,
    parse_semantic_scholar,
)
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

FIXTURES = Path(__file__).parent / "fixtures"
ARXIV_XML = (FIXTURES / "arxiv_response.xml").read_text(encoding="utf-8")
CROSSREF_JSON = json.loads((FIXTURES / "crossref_response.json").read_text(encoding="utf-8"))
OPENALEX_JSON = json.loads((FIXTURES / "openalex_response.json").read_text(encoding="utf-8"))
SEMANTIC_SCHOLAR_JSON = json.loads(
    (FIXTURES / "semantic_scholar_response.json").read_text(encoding="utf-8")
)


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'enrich.db'}")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            Work.__table__,
            MetadataAssertion.__table__,
            AuditEvent.__table__,
            # enrich_work reverse-rescans references + cached citing papers when it promotes
            # identifiers/title (the AppConfig singleton drives the fuzzy-as-confirmed toggle).
            AppConfig.__table__,
            Reference.__table__,
            ExternalPaper.__table__,
            ExternalCitationLink.__table__,
        ],
    )
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with session_local() as session:
        yield session


# --- parsers ----------------------------------------------------------------


def test_parse_arxiv_atom() -> None:
    meta = parse_arxiv_atom(ARXIV_XML)
    assert meta.source == "arxiv"
    assert meta.title == "Attention Is All You Need"
    assert meta.authors[:2] == ["Ashish Vaswani", "Noam Shazeer"]
    assert meta.year == 2017
    assert meta.doi == "10.48550/arXiv.1706.03762"
    assert "Transformer" in meta.abstract


def test_parse_crossref() -> None:
    meta = parse_crossref(CROSSREF_JSON)
    assert meta.source == "crossref"
    assert meta.title == "Deep Residual Learning for Image Recognition"
    assert meta.doi == "10.1109/cvpr.2016.90"
    assert meta.year == 2016
    assert meta.venue.startswith("2016 IEEE")
    assert meta.authors[0] == "Kaiming He"
    assert meta.abstract == "Deeper neural networks are more difficult to train."
    assert meta.citation_count == 189234  # is-referenced-by-count


def test_parse_crossref_empty() -> None:
    assert parse_crossref({}) is None


def test_parse_openalex_rebuilds_inverted_abstract() -> None:
    meta = parse_openalex(OPENALEX_JSON)
    assert meta.source == "openalex"
    assert meta.title == "Deep Residual Learning for Image Recognition"
    assert meta.doi == "10.1109/cvpr.2016.90"  # https://doi.org/ prefix stripped
    assert meta.year == 2016
    assert meta.venue.startswith("2016 IEEE")
    assert meta.authors == ["Kaiming He", "Xiangyu Zhang"]
    assert meta.abstract == "Deeper neural networks are harder to train."  # rebuilt in order
    assert meta.citation_count == 201457  # cited_by_count


def test_parse_openalex_empty() -> None:
    assert parse_openalex({}) is None


def test_parse_semantic_scholar() -> None:
    meta = parse_semantic_scholar(SEMANTIC_SCHOLAR_JSON)
    assert meta.source == "semanticscholar"
    assert meta.title == "Attention Is All You Need"
    assert meta.doi == "10.48550/arXiv.1706.03762"
    assert meta.year == 2017
    assert meta.venue == "Neural Information Processing Systems"
    assert meta.authors == ["Ashish Vaswani", "Noam Shazeer"]
    assert meta.citation_count == 105678  # citationCount


def test_parse_semantic_scholar_empty() -> None:
    assert parse_semantic_scholar({}) is None


def test_parsers_leave_citation_count_none_when_absent() -> None:
    # A source that omits its count field must yield None (not a crash / not 0), so a NULL snapshot.
    assert parse_crossref({"message": {"DOI": "10.1/x"}}).citation_count is None
    assert (
        parse_openalex({"id": "https://openalex.org/W1", "display_name": "x"}).citation_count
        is None
    )
    assert parse_semantic_scholar({"title": "x"}).citation_count is None


# --- enrich_work ------------------------------------------------------------


def _settings() -> Settings:
    return Settings(enrichment_enabled=True, enrichment_arxiv=True, enrichment_crossref=True)


def test_enrich_work_promotes_external_title_over_grobid(db_session) -> None:
    # Simulate a GROBID misparse (a footer captured as the title).
    work = Work(
        canonical_title="Provided proper attribution is provided, Google hereby grants...",
        normalized_title="provided proper attribution",
        canonical_metadata_source="grobid",
        arxiv_id="1706.03762",
        user_confirmed=False,
    )
    db_session.add(work)
    db_session.commit()

    result = enrich_work(
        db_session,
        work,
        settings=_settings(),
        arxiv_fetcher=lambda _id: parse_arxiv_atom(ARXIV_XML),
    )
    db_session.commit()

    assert result["sources"] == ["arxiv"]
    assert "title" in result["promoted"]
    assert work.canonical_title == "Attention Is All You Need"
    assert work.canonical_metadata_source == "arxiv"
    assert work.year == 2017
    title_assertion = db_session.scalar(
        select(MetadataAssertion).where(
            MetadataAssertion.field_name == "title", MetadataAssertion.source == "arxiv"
        )
    )
    assert title_assertion.selected_as_canonical is True
    assert db_session.scalars(
        select(AuditEvent).where(AuditEvent.event_type == "metadata.enrichment_called")
    ).all()


def test_enrich_work_respects_user_confirmed(db_session) -> None:
    work = Work(
        canonical_title="My Curated Title",
        normalized_title="my curated title",
        canonical_metadata_source="user",
        arxiv_id="1706.03762",
        user_confirmed=True,
    )
    db_session.add(work)
    db_session.commit()

    result = enrich_work(
        db_session,
        work,
        settings=_settings(),
        arxiv_fetcher=lambda _id: parse_arxiv_atom(ARXIV_XML),
    )
    db_session.commit()

    assert result["promoted"] == []
    assert work.canonical_title == "My Curated Title"  # not overwritten
    # Assertions still recorded for review.
    assert (
        db_session.scalar(
            select(MetadataAssertion).where(MetadataAssertion.field_name == "title")
        ).selected_as_canonical
        is False
    )


def test_enrich_work_crossref_by_doi(db_session) -> None:
    work = Work(canonical_title="x", normalized_title="x", doi="10.1109/cvpr.2016.90")
    db_session.add(work)
    db_session.commit()

    enrich_work(
        db_session,
        work,
        settings=_settings(),
        crossref_fetcher=lambda _doi, mailto=None: parse_crossref(CROSSREF_JSON),
    )
    db_session.commit()

    assert work.canonical_title == "Deep Residual Learning for Image Recognition"
    assert work.year == 2016


def test_enrich_work_uses_openalex_and_semantic_scholar_when_enabled(db_session) -> None:
    work = Work(
        canonical_title="x",
        normalized_title="x",
        doi="10.1109/cvpr.2016.90",
        arxiv_id="1706.03762",
    )
    db_session.add(work)
    db_session.commit()

    settings = Settings(
        enrichment_enabled=True,
        enrichment_arxiv=False,
        enrichment_crossref=False,
        enrichment_openalex=True,
        enrichment_semantic_scholar=True,
    )
    result = enrich_work(
        db_session,
        work,
        settings=settings,
        openalex_fetcher=lambda _doi, mailto=None: parse_openalex(OPENALEX_JSON),
        semantic_scholar_fetcher=lambda arxiv_id=None, doi=None: parse_semantic_scholar(
            SEMANTIC_SCHOLAR_JSON
        ),
    )
    db_session.commit()

    assert set(result["sources"]) == {"openalex", "semanticscholar"}
    recorded = {a.source for a in db_session.scalars(select(MetadataAssertion)).all()}
    assert {"openalex", "semanticscholar"} <= recorded
    called = db_session.scalars(
        select(AuditEvent).where(AuditEvent.event_type == "metadata.enrichment_called")
    ).all()
    assert {event.details["source"] for event in called} == {"openalex", "semanticscholar"}


def test_enrich_work_new_sources_are_off_by_default(db_session) -> None:
    work = Work(canonical_title="x", normalized_title="x", doi="10.1109/cvpr.2016.90")
    db_session.add(work)
    db_session.commit()

    def _must_not_call(*_args, **_kwargs):
        raise AssertionError("opt-in source was queried while disabled")

    result = enrich_work(
        db_session,
        work,
        settings=Settings(
            enrichment_enabled=True, enrichment_arxiv=False, enrichment_crossref=False
        ),
        openalex_fetcher=_must_not_call,
        semantic_scholar_fetcher=_must_not_call,
    )
    assert result["sources"] == []


def test_enrich_work_is_idempotent(db_session) -> None:
    work = Work(canonical_title="x", normalized_title="x", arxiv_id="1706.03762")
    db_session.add(work)
    db_session.commit()
    fetch = lambda _id: parse_arxiv_atom(ARXIV_XML)  # noqa: E731
    enrich_work(db_session, work, settings=_settings(), arxiv_fetcher=fetch)
    db_session.commit()
    count1 = db_session.scalar(select(func.count()).select_from(MetadataAssertion))
    ids1 = set(db_session.scalars(select(MetadataAssertion.id)).all())
    enrich_work(db_session, work, settings=_settings(), arxiv_fetcher=fetch)
    db_session.commit()
    count2 = db_session.scalar(select(func.count()).select_from(MetadataAssertion))
    assert count1 == count2
    # 1b: identical re-enrich reuses the same rows (no id/timestamp churn), not delete+reinsert.
    assert set(db_session.scalars(select(MetadataAssertion.id)).all()) == ids1


def test_enrich_work_without_identifier_is_noop(db_session) -> None:
    work = Work(canonical_title="x", normalized_title="x")
    db_session.add(work)
    db_session.commit()
    result = enrich_work(db_session, work, settings=_settings())
    assert result["sources"] == []
    assert db_session.scalar(select(func.count()).select_from(MetadataAssertion)) == 0


def test_enrich_work_continues_past_a_failing_source(db_session) -> None:
    """D8: arXiv raising must not stop Crossref from being tried; the failure is recorded."""
    work = Work(
        canonical_title="x",
        normalized_title="x",
        doi="10.1109/cvpr.2016.90",
        arxiv_id="1706.03762",
    )
    db_session.add(work)
    db_session.commit()

    def _boom(_id):
        raise RuntimeError("arXiv is down")

    result = enrich_work(
        db_session,
        work,
        settings=_settings(),
        arxiv_fetcher=_boom,
        crossref_fetcher=lambda _doi, mailto=None: parse_crossref(CROSSREF_JSON),
    )
    db_session.commit()

    assert result["sources"] == ["crossref"]  # crossref still ran
    assert result["failed"] == ["arxiv"]  # the failure is surfaced, not raised
    assert work.canonical_title == "Deep Residual Learning for Image Recognition"


def test_enrich_work_reports_no_failures_on_clean_run(db_session) -> None:
    work = Work(canonical_title="x", normalized_title="x", arxiv_id="1706.03762")
    db_session.add(work)
    db_session.commit()
    result = enrich_work(
        db_session,
        work,
        settings=_settings(),
        arxiv_fetcher=lambda _id: parse_arxiv_atom(ARXIV_XML),
    )
    assert result["failed"] == []


# --- citation-count snapshot (Track C P1) -----------------------------------


def test_enrich_work_snapshots_citation_count_by_source_priority(db_session) -> None:
    # Both OpenAlex and Crossref report a count; OpenAlex outranks Crossref in the priority order.
    work = Work(canonical_title="x", normalized_title="x", doi="10.1109/cvpr.2016.90")
    db_session.add(work)
    db_session.commit()
    settings = Settings(
        enrichment_enabled=True,
        enrichment_arxiv=False,
        enrichment_crossref=True,
        enrichment_openalex=True,
    )
    enrich_work(
        db_session,
        work,
        settings=settings,
        crossref_fetcher=lambda _doi, mailto=None: parse_crossref(CROSSREF_JSON),
        openalex_fetcher=lambda _doi, mailto=None: parse_openalex(OPENALEX_JSON),
    )
    db_session.commit()

    assert work.citation_count == 201457  # OpenAlex value, not Crossref's 189234
    assert work.citation_count_source == "openalex"
    assert work.citation_count_fetched_at is not None


def test_enrich_work_falls_back_to_lower_priority_count(db_session) -> None:
    # Only Crossref runs → its count is used even though OpenAlex outranks it in general.
    work = Work(canonical_title="x", normalized_title="x", doi="10.1109/cvpr.2016.90")
    db_session.add(work)
    db_session.commit()
    enrich_work(
        db_session,
        work,
        settings=_settings(),
        crossref_fetcher=lambda _doi, mailto=None: parse_crossref(CROSSREF_JSON),
    )
    db_session.commit()

    assert work.citation_count == 189234
    assert work.citation_count_source == "crossref"


def test_enrich_work_leaves_citation_count_null_without_identifier(db_session) -> None:
    work = Work(canonical_title="x", normalized_title="x")
    db_session.add(work)
    db_session.commit()
    enrich_work(db_session, work, settings=_settings())
    assert work.citation_count is None
    assert work.citation_count_source is None
    assert work.citation_count_fetched_at is None
