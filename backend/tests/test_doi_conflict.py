"""Unit tests for the shared DOI-collision helpers (issue 3 / batch 8).

These cover the ``uq_works_doi`` recognition + message building used by both the worker jobs and
the works endpoints. The unique index itself lives only in the Postgres migration (partial index
with a ``postgresql_where`` clause), so a real collision cannot be provoked against the SQLite test
DB — the recognition path is exercised with a synthetic ``IntegrityError`` shaped like psycopg's,
mirroring ``test_d7_extraction_recovery``.
"""

from sqlalchemy.exc import IntegrityError

from app.models.work import Work
from app.services.doi_conflict import (
    conflict_message,
    doi_conflict_detail,
    doi_from_detail,
    message_from_exception,
)


def _doi_integrity_error(detail: str = "Key (doi)=(10.1/dup) already exists."):
    class _Diag:
        constraint_name = "uq_works_doi"
        message_detail = detail

    class _Orig(Exception):
        diag = _Diag()

    return IntegrityError("UPDATE works SET doi=...", {}, _Orig("duplicate key"))


def test_doi_conflict_detail_recognises_uq_works_doi() -> None:
    exc = _doi_integrity_error()
    assert doi_conflict_detail(exc) == "Key (doi)=(10.1/dup) already exists."


def test_doi_conflict_detail_ignores_other_constraints() -> None:
    class _Diag:
        constraint_name = "some_other_uq"
        message_detail = "x"

    class _Orig(Exception):
        diag = _Diag()

    assert doi_conflict_detail(IntegrityError("INSERT", {}, _Orig("other"))) is None


def test_doi_from_detail_extracts_value() -> None:
    assert doi_from_detail("Key (doi)=(10.1234/foo.bar) already exists.") == "10.1234/foo.bar"
    assert doi_from_detail("no doi here") is None
    assert doi_from_detail(None) is None


def test_conflict_message_names_offending_doi(db) -> None:
    msg = conflict_message(db, doi="10.1/dup")
    assert "Offending DOI: 10.1/dup" in msg
    assert "another paper" in msg


def test_conflict_message_names_existing_paper_title(db) -> None:
    db.add(Work(canonical_title="The Real Owner Paper", normalized_title="the real owner paper", doi="10.1/dup"))
    db.commit()
    msg = conflict_message(db, doi="10.1/dup")
    assert "The Real Owner Paper" in msg
    assert "Offending DOI: 10.1/dup" in msg


def test_conflict_message_without_doi_is_still_actionable(db) -> None:
    msg = conflict_message(db, doi=None)
    assert "another paper" in msg
    assert "Offending DOI" not in msg


def test_message_from_exception_roundtrip(db) -> None:
    db.add(Work(canonical_title="Owner", normalized_title="owner", doi="10.1/dup"))
    db.commit()
    msg = message_from_exception(db, _doi_integrity_error())
    assert msg is not None
    assert "Offending DOI: 10.1/dup" in msg and "Owner" in msg


def test_message_from_exception_returns_none_for_non_doi_error(db) -> None:
    class _Diag:
        constraint_name = "other"
        message_detail = "x"

    class _Orig(Exception):
        diag = _Diag()

    assert message_from_exception(db, IntegrityError("INSERT", {}, _Orig("x"))) is None
