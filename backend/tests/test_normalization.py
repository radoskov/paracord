"""Normalization-helper regression tests (batch 12).

Covers the two fixes that make identifier/title matching tolerant of real-world formatting:
- ``normalize_title`` must strip punctuation before collapsing whitespace, so a spaced en-dash
  separator compares equal to the colon form (the motivating "KnowRob" citation-mismatch case).
- ``normalize_doi`` / ``split_arxiv_id`` must strip the http(s)/resolver-host/scheme decorations
  seen in extracted references so the identifier gate does not silently fail on prefixed ids.
"""

from app.services.duplicate_detection import split_arxiv_id
from app.utils.normalization import normalize_doi, normalize_title


def test_normalize_title_dash_and_colon_forms_match() -> None:
    colon = "KnowRob: A knowledge processing infrastructure for cognition-enabled robots"
    dash = "KnowRob – A Knowledge Processing Infrastructure for Cognition-enabled Robots"
    assert normalize_title(colon) == normalize_title(dash)
    # No double spaces survive the dash removal.
    assert "  " not in normalize_title(dash)


def test_normalize_title_collapses_and_lowercases() -> None:
    assert normalize_title("  Hello,   WORLD!  ") == "hello world"


def test_normalize_doi_strips_scheme_and_resolver_hosts() -> None:
    bare = "10.1007/s10514-016-9587-8"
    for variant in (
        bare,
        f"https://doi.org/{bare}",
        f"http://doi.org/{bare}",
        f"https://dx.doi.org/{bare}",
        f"http://dx.doi.org/{bare}",
        f"doi:{bare}",
        f"DOI:{bare}".upper(),
    ):
        assert normalize_doi(variant) == bare


def test_split_arxiv_id_strips_scheme_path_and_suffix() -> None:
    base = "1706.03762"
    for variant in (
        base,
        f"arXiv:{base}",
        f"arxiv:{base}",
        f"https://arxiv.org/abs/{base}",
        f"http://arxiv.org/abs/{base}",
        f"https://arxiv.org/pdf/{base}",
        f"https://arxiv.org/pdf/{base}.pdf",
    ):
        assert split_arxiv_id(variant)["base"] == base


def test_split_arxiv_id_keeps_version_suffix() -> None:
    parsed = split_arxiv_id("arXiv:1706.03762v5")
    assert parsed["base"] == "1706.03762"
    assert parsed["version"] == "v5"
