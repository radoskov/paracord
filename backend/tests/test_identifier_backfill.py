"""arXiv id / DOI backfill on web-sourced and enriched works (Batch P, P3).

Previously a paper obtained via find-on-web kept empty ``arxiv_id``/``doi`` (the candidate's
identifiers were dropped), and enrichment could never set ``arxiv_id``. These tests pin the fix:
the identifiers are persisted when known and the field is empty, while user-locked values are
never overwritten. (The find-on-web download path itself is covered in ``test_web_find.py`` where
the download harness lives.)
"""

from app.core.config import Settings
from app.models.work import Work
from app.services.identifiers import backfill_identifiers
from app.services.metadata_enrichment import ExternalMetadata, enrich_work, parse_arxiv_atom


def _no_source(*_args, **_kwargs):
    return None


# --- unit: the shared helper -------------------------------------------------


def test_backfill_fills_empty_and_normalizes():
    work = Work(canonical_title="w")
    filled = backfill_identifiers(work, doi="HTTPS://doi.org/10.1/ABC", arxiv_id="2101.00001v2")
    assert set(filled) == {"doi", "arxiv_id"}
    assert work.doi == "10.1/abc"  # normalized (lowercased, resolver prefix stripped)
    assert work.arxiv_id == "2101.00001v2"
    assert work.arxiv_base_id == "2101.00001"  # version stripped


def test_backfill_does_not_overwrite_existing_or_locked():
    work = Work(canonical_title="w", doi="10.9/existing", arxiv_id=None)
    work.confirmed_fields = ["arxiv_id"]  # user-locked
    filled = backfill_identifiers(work, doi="10.1/new", arxiv_id="2101.00001")
    # doi already set → untouched; arxiv_id locked → untouched.
    assert filled == []
    assert work.doi == "10.9/existing"
    assert work.arxiv_id is None


# --- enrichment path ---------------------------------------------------------


def test_parse_arxiv_atom_extracts_arxiv_id():
    xml = """<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>http://arxiv.org/abs/1706.03762v5</id>
        <title>Attention Is All You Need</title>
        <summary>We propose the Transformer.</summary>
        <published>2017-06-12T00:00:00Z</published>
      </entry>
    </feed>"""
    meta = parse_arxiv_atom(xml)
    assert meta is not None
    assert meta.arxiv_id == "1706.03762v5"


def test_enrichment_backfills_arxiv_id_from_provider(db):
    # enrich_work mutates the in-session work (the worker commits), so we assert on the object.
    work = Work(canonical_title="Some paper", doi="10.1/x", arxiv_id=None)
    db.add(work)
    db.flush()

    def fake_ss(*, arxiv_id=None, doi=None, **_kwargs):
        return ExternalMetadata(source="semanticscholar", title="Some paper", arxiv_id="2101.09999")

    result = enrich_work(
        db,
        work,
        settings=Settings(enrichment_semantic_scholar=True),
        arxiv_fetcher=_no_source,
        crossref_fetcher=_no_source,
        openalex_fetcher=_no_source,
        semantic_scholar_fetcher=fake_ss,
    )
    assert work.arxiv_id == "2101.09999"
    assert "arxiv_id" in result["promoted"]


def test_enrichment_does_not_overwrite_locked_arxiv_id(db):
    work = Work(canonical_title="Some paper", doi="10.1/x", arxiv_id="1111.11111")
    work.confirmed_fields = ["arxiv_id"]
    db.add(work)
    db.flush()

    def fake_ss(*, arxiv_id=None, doi=None, **_kwargs):
        return ExternalMetadata(source="semanticscholar", title="Some paper", arxiv_id="2222.22222")

    result = enrich_work(
        db,
        work,
        settings=Settings(enrichment_semantic_scholar=True),
        arxiv_fetcher=_no_source,
        crossref_fetcher=_no_source,
        openalex_fetcher=_no_source,
        semantic_scholar_fetcher=fake_ss,
    )
    assert work.arxiv_id == "1111.11111"  # locked → untouched
    assert "arxiv_id" not in result["promoted"]
