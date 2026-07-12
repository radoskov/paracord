"""Author-name matching for reference→library matching (batch 12, owner items #5/#6)."""

from app.services.author_matching import (
    author_overlap_ratio,
    names_match,
    parse_author_name,
)


def test_parse_handles_both_orderings_and_diacritics() -> None:
    assert parse_author_name("London, Jack") == ("london", "j")
    assert parse_author_name("Jack London") == ("london", "j")
    assert parse_author_name("J. London") == ("london", "j")
    assert parse_author_name("London, J.") == ("london", "j")
    # A lone surname has no initial.
    assert parse_author_name("London") == ("london", None)
    # Diacritics fold: "Å" -> "a".
    assert parse_author_name("Ångström, Anders") == ("angstrom", "a")
    assert parse_author_name("   ") is None


def test_names_match_surname_and_initial_rules() -> None:
    jack = parse_author_name("London, Jack")
    # Same surname, agreeing initial (J == J).
    assert names_match(jack, parse_author_name("J. London"))
    assert names_match(jack, parse_author_name("London, J."))
    # Same surname, one side has no initial → still a match.
    assert names_match(jack, parse_author_name("London"))
    # Same surname, disagreeing initial (R != J) → NOT a match (owner example).
    assert not names_match(parse_author_name("R. London"), parse_author_name("Jack London"))
    # Different surname → never a match.
    assert not names_match(jack, parse_author_name("Jack Berlin"))


def test_overlap_ratio_without_et_al_is_ref_side_fraction() -> None:
    ref = ["Smith, J.", "Nobody, X."]
    work = ["John Smith", "Alice Doe"]
    assert author_overlap_ratio(ref, work) == 0.5  # 1 of 2 reference authors matched
    assert author_overlap_ratio(["Smith, J."], ["John Smith", "Alice Doe"]) == 1.0
    assert author_overlap_ratio(["Berlin, Z."], ["John Smith"]) == 0.0


def test_overlap_ratio_with_et_al_validates_single_best_author() -> None:
    # "et al" (#6): one confirmed shared author is enough, regardless of the truncated rest.
    assert (
        author_overlap_ratio(["Smith, J.", "et al."], ["John Smith", "Alice Doe", "Bob Roe"]) == 1.0
    )
    # ...but the one named author must actually match.
    assert author_overlap_ratio(["Berlin, Z.", "et al."], ["John Smith", "Alice Doe"]) == 0.0


def test_overlap_ratio_zero_when_a_side_is_empty() -> None:
    assert author_overlap_ratio([], ["John Smith"]) == 0.0
    assert author_overlap_ratio(["Smith, J."], []) == 0.0
