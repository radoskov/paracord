"""Canonical-reference dedup key (batch 12)."""

from app.services.reference_links import reference_dedup_key


def test_dedup_key_prefers_doi_then_arxiv_then_title() -> None:
    assert reference_dedup_key(doi="10.1/x", normalized_title="t", year=2020) == "doi:10.1/x"
    assert reference_dedup_key(arxiv_id="2101.00001", normalized_title="t") == "arxiv:2101.00001"
    assert reference_dedup_key(normalized_title="knowrob", year=2015) == "title:knowrob|2015"
    assert reference_dedup_key(normalized_title="knowrob", year=None) == "title:knowrob|"
    assert reference_dedup_key() is None


def test_dedup_key_is_capped_to_fit_the_indexed_column() -> None:
    """A mis-parsed reference whose whole citation lands in ``title`` must not overflow the
    ``String(512)`` dedup_key column (which would abort extraction + the 0059 backfill migration)."""
    long_title = "a" * 900
    key = reference_dedup_key(normalized_title=long_title, year=2020)
    assert key is not None
    assert len(key) <= 512
    assert key.endswith("|2020")  # the year suffix survives the truncation
    # Deterministic: the same long title always yields the same (capped) key so dedup still works.
    assert key == reference_dedup_key(normalized_title=long_title, year=2020)


def test_dedup_key_treats_arxiv_doi_as_arxiv_base() -> None:
    """An arXiv DOI and a bare arXiv id spell the same paper — one canonical key for both."""
    assert reference_dedup_key(doi="10.48550/arXiv.2101.00001") == "arxiv:2101.00001"
    assert reference_dedup_key(doi="https://doi.org/10.48550/arXiv.2101.00001v2") == (
        "arxiv:2101.00001"
    )
    assert reference_dedup_key(doi="10.48550/arXiv.2101.00001") == reference_dedup_key(
        arxiv_id="arXiv:2101.00001v1"
    )
    # An ordinary DOI still keys as a DOI.
    assert reference_dedup_key(doi="10.1145/3292500") == "doi:10.1145/3292500"


def test_find_or_create_finds_legacy_arxiv_doi_row(db) -> None:
    """Rows written before the arXiv-DOI bridge keyed as ``doi:10.48550/arxiv.<b>`` must still be
    found (no duplicate canonical row on live data)."""
    from app.models.citation import Reference
    from app.services.reference_links import find_or_create_reference

    legacy = Reference(
        title="Old Row",
        doi="10.48550/arxiv.2101.00001",
        dedup_key="doi:10.48550/arxiv.2101.00001",
        resolution_status="external",
    )
    db.add(legacy)
    db.flush()

    found = find_or_create_reference(
        db,
        title="Old Row",
        doi="10.48550/arXiv.2101.00001",
        arxiv_id=None,
        year=None,
        raw_citation=None,
        authors=None,
    )
    assert found.id == legacy.id
