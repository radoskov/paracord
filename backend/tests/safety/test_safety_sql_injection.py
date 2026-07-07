"""SQL-injection probes (Batch S): the search-query parser is a safe allowlist that only carries
values (bound through the ORM), the sort key is allowlisted, and the dynamic pgvector-column
registry derives every column name through a strict slug allowlist. Malicious identifiers/queries
must never break out into executable SQL.
"""

from __future__ import annotations

import pytest
from app.services.embedding_registry import _SAFE_COLUMN, slugify
from app.services.search_query import parse_search_query

pytestmark = pytest.mark.safety

# Literal-equality payloads (no embedded double-quote, which would re-trigger shlex quoting — an
# unrelated tokenization concern, not a safety one).
_INJECTIONS = [
    "'; DROP TABLE works; --",
    "1 OR 1=1",
    "0); DELETE FROM works; --",
    "' UNION SELECT password_hash FROM users --",
]


@pytest.mark.parametrize("payload", _INJECTIONS)
def test_search_query_carries_injection_as_plain_value(payload: str) -> None:
    parsed = parse_search_query(f'author:"{payload}"')
    # The value is carried verbatim as a string (later bound through the ORM), never interpreted.
    assert parsed.author == payload


def test_search_query_unknown_key_falls_back_to_free_text() -> None:
    parsed = parse_search_query("evil_col:DROP")
    assert parsed.author is None
    assert "evil_col:DROP" in parsed.text


def test_search_query_year_operator_ignores_non_numeric_injection() -> None:
    parsed = parse_search_query("year:2020;DROP")
    # The year regex rejects the malformed value → no bounds set, nothing interpolated.
    assert parsed.year_min is None
    assert parsed.year_max is None


@pytest.mark.parametrize(
    "model_name",
    [
        "nomic; DROP TABLE work_chunks; --",
        "vec_x'); DELETE FROM users; --",
        "../../etc/passwd",
        "Model Name With Spaces!!",
        "ollama:mxbai-embed-large",
    ],
)
def test_slugify_produces_only_safe_column(model_name: str) -> None:
    slug = slugify(model_name)
    column = f"vec_{slug}"
    assert _SAFE_COLUMN.match(column), f"unsafe column derived: {column!r}"
    # No SQL metacharacters survive slugification.
    assert not any(c in slug for c in " ;'\"()-/.")


@pytest.mark.parametrize(
    "column",
    ["vec_x; DROP TABLE", "vec_x'", "work_chunks", "vec_ x", "DROP", "vec_x)"],
)
def test_safe_column_regex_rejects_injection(column: str) -> None:
    assert _SAFE_COLUMN.match(column) is None


# --- HTTP-level: an injection payload through /works must not error or corrupt the table ---------


def test_works_search_injection_is_harmless(client, auth_headers, make_work) -> None:
    make_work("keeper-work")
    headers = auth_headers("reader")
    resp = client.get(
        "/api/v1/works",
        headers=headers,
        params={"q": 'author:"\'; DROP TABLE works; --"'},
    )
    assert resp.status_code == 200
    # The table still exists and is queryable afterwards.
    after = client.get("/api/v1/works", headers=headers)
    assert after.status_code == 200
    assert any(w["canonical_title"] == "keeper-work" for w in after.json()["items"])


def test_works_sort_key_injection_falls_back(client, auth_headers, make_work) -> None:
    make_work("sortable")
    resp = client.get(
        "/api/v1/works",
        headers=auth_headers("reader"),
        params={"sort": "canonical_title; DROP TABLE works"},
    )
    assert resp.status_code == 200
