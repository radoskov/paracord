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
