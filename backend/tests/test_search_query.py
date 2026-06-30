"""Structured search-query parsing + list_works operators (SPEC §8.7/§14)."""

from app.services.search_query import parse_search_query


def test_parses_operators_and_free_text():
    p = parse_search_query('attention author:"Jane Doe" year:>=2019 has:pdf tag:ml venue:NeurIPS')
    assert p.text == "attention"
    assert p.author == "Jane Doe"
    assert p.year_min == 2019 and p.year_max is None
    assert p.has_pdf is True
    assert p.tag == "ml"
    assert p.venue == "NeurIPS"


def test_year_forms():
    assert parse_search_query("year:2020").year_min == 2020
    assert parse_search_query("year:2020").year_max == 2020
    assert parse_search_query("year:<=2021").year_max == 2021
    r = parse_search_query("year:2019-2021")
    assert (r.year_min, r.year_max) == (2019, 2021)


def test_unknown_operator_is_free_text():
    p = parse_search_query("foo:bar baz")
    assert "foo:bar" in p.text and "baz" in p.text


# --- endpoint integration ---------------------------------------------------


def _make(client, headers, **kw):
    return client.post("/api/v1/works", headers=headers, json=kw).json()


def test_list_works_year_and_title_operators(client, auth_headers):
    h = auth_headers("editor")
    _make(client, h, canonical_title="Old transformer paper", year=2015)
    _make(client, h, canonical_title="New transformer paper", year=2022)
    _make(client, h, canonical_title="Unrelated baking", year=2022)

    reader = auth_headers("reader")
    got = client.get("/api/v1/works?q=transformer year:>=2020", headers=reader).json()
    titles = {w["canonical_title"] for w in got}
    assert titles == {"New transformer paper"}


def test_list_works_has_pdf_operator(client, auth_headers):
    h = auth_headers("editor")
    _make(client, h, canonical_title="No-file paper xyz")
    got = client.get("/api/v1/works?q=xyz has:no-pdf", headers=h).json()
    assert any(w["canonical_title"] == "No-file paper xyz" for w in got)
