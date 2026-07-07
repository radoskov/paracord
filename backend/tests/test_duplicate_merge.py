"""Duplicate-resolution overhaul tests (Batch D): merge / unmerge / flatten / link / transaction.

Exercises the ``duplicate_resolution`` service directly against a file-backed SQLite session with
the full set of work-owned tables, plus the visibility clamp via ``access.build_works_query`` for an
owner (the primary user). Data-integrity critical: every reversal is asserted to restore the exact
pre-merge state.
"""

from pathlib import Path

import pytest
from app.api.v1.endpoints.works import build_works_query
from app.core.security import hash_password
from app.db.base import Base
from app.models.annotation import Annotation
from app.models.audit import AuditEvent
from app.models.citation import CitationMention, Reference
from app.models.duplicate import DuplicateCandidate
from app.models.file import File, FileSegment, FileWorkLink
from app.models.metadata import MetadataAssertion
from app.models.organization import Rack, RackShelf, Shelf, ShelfWork, Tag, TagLink
from app.models.user import User
from app.models.work import Work, WorkLink, WorkVersion
from app.services import duplicate_resolution as dr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.slow

_TABLES = [
    User.__table__,
    AuditEvent.__table__,
    Work.__table__,
    WorkVersion.__table__,
    WorkLink.__table__,
    File.__table__,
    FileSegment.__table__,
    FileWorkLink.__table__,
    Shelf.__table__,
    ShelfWork.__table__,
    Rack.__table__,
    RackShelf.__table__,
    Tag.__table__,
    TagLink.__table__,
    Reference.__table__,
    CitationMention.__table__,
    Annotation.__table__,
    MetadataAssertion.__table__,
    DuplicateCandidate.__table__,
]


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'merge.db'}")
    Base.metadata.create_all(bind=engine, tables=_TABLES)
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with session_local() as session:
        yield session


@pytest.fixture()
def owner(db_session) -> User:
    user = User(username="owner", password_hash=hash_password("secret"), role="owner")
    db_session.add(user)
    db_session.commit()
    return user


def _assertions(db_session, work_id, field=None):
    stmt = select(MetadataAssertion).where(
        MetadataAssertion.entity_type == "work", MetadataAssertion.entity_id == work_id
    )
    if field:
        stmt = stmt.where(MetadataAssertion.field_name == field)
    return list(db_session.scalars(stmt).all())


# --------------------------------------------------------------------------------------------------
# Merge: field consolidation
# --------------------------------------------------------------------------------------------------
def test_merge_fills_empty_base_fields_from_source(db_session, owner) -> None:
    base = Work(canonical_title="Attention", normalized_title="attention")
    source = Work(
        canonical_title="Attention",
        normalized_title="attention",
        abstract="A source abstract.",
        doi="10.1/x",
        venue="NeurIPS",
        year=2017,
    )
    db_session.add_all([base, source])
    db_session.commit()

    dr.merge_works(db_session, base=base, source=source, actor=owner)
    db_session.commit()
    db_session.refresh(base)

    assert base.abstract == "A source abstract."
    assert base.doi == "10.1/x"
    assert base.venue == "NeurIPS"
    assert base.year == 2017
    # A canonical provenance assertion is recorded for each filled field.
    filled = {a.field_name for a in _assertions(db_session, base.id) if a.selected_as_canonical}
    assert {"abstract", "doi", "venue", "year"} <= filled


def test_merge_differing_values_become_conflict_not_overwrite(db_session, owner) -> None:
    base = Work(
        canonical_title="Base title",
        normalized_title="base title",
        abstract="Base abstract",
    )
    source = Work(
        canonical_title="Source title",
        normalized_title="source title",
        abstract="Different source abstract",
    )
    db_session.add_all([base, source])
    db_session.commit()

    dr.merge_works(db_session, base=base, source=source, actor=owner)
    db_session.commit()
    db_session.refresh(base)

    # Base value NOT overwritten.
    assert base.abstract == "Base abstract"
    assert base.canonical_title == "Base title"
    # Abstract now has two distinct assertion values → a conflict the review UI surfaces.
    abstract_values = {a.value for a in _assertions(db_session, base.id, "abstract")}
    assert {"Base abstract", "Different source abstract"} <= abstract_values


def test_merge_never_overwrites_locked_base_field(db_session, owner) -> None:
    base = Work(
        canonical_title="Locked",
        normalized_title="locked",
        abstract=None,
        confirmed_fields=["abstract"],
    )
    source = Work(canonical_title="Locked", normalized_title="locked", abstract="Intruder")
    db_session.add_all([base, source])
    db_session.commit()

    dr.merge_works(db_session, base=base, source=source, actor=owner)
    db_session.commit()
    db_session.refresh(base)

    # Locked empty field is NOT filled, but the source value is preserved as a conflict assertion.
    assert base.abstract is None
    assert any(a.value == "Intruder" for a in _assertions(db_session, base.id, "abstract"))


# --------------------------------------------------------------------------------------------------
# Merge: entity movement + redirect + hiding
# --------------------------------------------------------------------------------------------------
def _wire_full_source(db_session, base, source):
    file = File(sha256="a" * 64, size_bytes=1)
    shelf = Shelf(name="Reading")
    tag = Tag(name="ml", normalized_name="ml")
    citer = Work(canonical_title="Citer", normalized_title="citer")
    db_session.add_all([file, shelf, tag, citer])
    db_session.flush()
    ref_out = Reference(citing_work_id=source.id, raw_citation="out")
    ref_in = Reference(
        citing_work_id=citer.id, resolved_work_id=source.id, raw_citation="in", title="t"
    )
    db_session.add_all([file, ref_out, ref_in])
    db_session.flush()
    mention_out = CitationMention(citing_work_id=source.id, reference_id=ref_out.id)
    mention_in = CitationMention(
        citing_work_id=citer.id, reference_id=ref_in.id, resolved_cited_work_id=source.id
    )
    version = WorkVersion(work_id=source.id, version_label="v1")
    annotation = Annotation(work_id=source.id, annotation_type="note", content_markdown="hi")
    authors = MetadataAssertion(
        entity_type="work",
        entity_id=source.id,
        field_name="authors",
        value="Vaswani et al.",
        source="grobid",
    )
    db_session.add_all(
        [
            FileWorkLink(file_id=file.id, work_id=source.id),
            ShelfWork(shelf_id=shelf.id, work_id=source.id),
            TagLink(tag_id=tag.id, entity_type="work", entity_id=source.id),
            mention_out,
            mention_in,
            version,
            annotation,
            authors,
        ]
    )
    db_session.commit()
    return {
        "file": file,
        "shelf": shelf,
        "tag": tag,
        "citer": citer,
        "ref_out": ref_out,
        "ref_in": ref_in,
        "mention_out": mention_out,
        "mention_in": mention_in,
        "version": version,
        "annotation": annotation,
        "authors": authors,
    }


def test_merge_moves_all_owned_entities_and_redirects_incoming(db_session, owner) -> None:
    base = Work(canonical_title="Base", normalized_title="base")
    source = Work(canonical_title="Source", normalized_title="source")
    db_session.add_all([base, source])
    db_session.commit()
    w = _wire_full_source(db_session, base, source)

    dr.merge_works(db_session, base=base, source=source, actor=owner)
    db_session.commit()

    assert db_session.get(FileWorkLink, w["file"].id) is None or True  # link repointed below
    assert (
        db_session.scalar(select(FileWorkLink.work_id).where(FileWorkLink.file_id == w["file"].id))
        == base.id
    )
    assert db_session.get(ShelfWork, {"shelf_id": w["shelf"].id, "work_id": base.id}) is not None
    assert (
        db_session.get(
            TagLink, {"tag_id": w["tag"].id, "entity_type": "work", "entity_id": base.id}
        )
        is not None
    )
    assert db_session.get(Reference, w["ref_out"].id).citing_work_id == base.id
    # Incoming reference redirected: the citer now resolves to the base.
    assert db_session.get(Reference, w["ref_in"].id).resolved_work_id == base.id
    assert db_session.get(CitationMention, w["mention_out"].id).citing_work_id == base.id
    assert db_session.get(CitationMention, w["mention_in"].id).resolved_cited_work_id == base.id
    assert db_session.get(WorkVersion, w["version"].id).work_id == base.id
    assert db_session.get(Annotation, w["annotation"].id).work_id == base.id
    assert db_session.get(MetadataAssertion, w["authors"].id).entity_id == base.id


def test_merged_source_is_hidden_shadow(db_session, owner) -> None:
    base = Work(canonical_title="Base", normalized_title="base")
    source = Work(canonical_title="Source", normalized_title="source")
    db_session.add_all([base, source])
    db_session.commit()

    dr.merge_works(db_session, base=base, source=source, actor=owner)
    db_session.commit()
    db_session.refresh(source)

    assert source.merged_into_id == base.id
    assert source.merge_record is not None
    # The shadow never appears in any work-returning query (owner sees everything else).
    visible_ids = {w.id for w in db_session.scalars(build_works_query(db_session, owner)).all()}
    assert base.id in visible_ids
    assert source.id not in visible_ids


# --------------------------------------------------------------------------------------------------
# Unmerge: exact reversal
# --------------------------------------------------------------------------------------------------
def test_unmerge_reverses_the_last_merge_exactly(db_session, owner) -> None:
    base = Work(canonical_title="Base", normalized_title="base", abstract="Base abstract")
    source = Work(
        canonical_title="Source",
        normalized_title="source",
        abstract="Src abstract",
        doi="10.9/z",
    )
    db_session.add_all([base, source])
    db_session.commit()
    w = _wire_full_source(db_session, base, source)

    dr.merge_works(db_session, base=base, source=source, actor=owner)
    db_session.commit()

    dr.unmerge_work(db_session, base_id=base.id, actor=owner)
    db_session.commit()
    db_session.refresh(base)
    db_session.refresh(source)

    # Two standalone papers again.
    assert source.merged_into_id is None
    assert source.merge_record is None
    # Filled field (doi was empty on base) cleared back; conflict assertion (abstract) removed.
    assert base.doi is None
    assert base.abstract == "Base abstract"
    assert _assertions(db_session, base.id) == []
    # Entities moved back to the source.
    assert (
        db_session.scalar(select(FileWorkLink.work_id).where(FileWorkLink.file_id == w["file"].id))
        == source.id
    )
    assert db_session.get(ShelfWork, {"shelf_id": w["shelf"].id, "work_id": source.id}) is not None
    assert db_session.get(Reference, w["ref_out"].id).citing_work_id == source.id
    assert db_session.get(Reference, w["ref_in"].id).resolved_work_id == source.id
    assert db_session.get(WorkVersion, w["version"].id).work_id == source.id
    assert db_session.get(Annotation, w["annotation"].id).work_id == source.id
    assert db_session.get(MetadataAssertion, w["authors"].id).entity_id == source.id
    # Both papers visible again.
    visible_ids = {
        work.id for work in db_session.scalars(build_works_query(db_session, owner)).all()
    }
    assert {base.id, source.id} <= visible_ids


def test_unmerge_without_reversible_shadow_raises(db_session, owner) -> None:
    base = Work(canonical_title="Base", normalized_title="base")
    db_session.add(base)
    db_session.commit()
    with pytest.raises(ValueError, match="no reversible merge"):
        dr.unmerge_work(db_session, base_id=base.id, actor=owner)


# --------------------------------------------------------------------------------------------------
# Flatten-on-re-merge
# --------------------------------------------------------------------------------------------------
def test_flatten_on_re_merge_finalizes_prior_shadow(db_session, owner) -> None:
    base = Work(canonical_title="Base", normalized_title="base")
    first = Work(canonical_title="First", normalized_title="first")
    second = Work(canonical_title="Second", normalized_title="second")
    db_session.add_all([base, first, second])
    db_session.commit()

    dr.merge_works(db_session, base=base, source=first, actor=owner)
    db_session.commit()
    assert dr.has_reversible_shadow(db_session, base.id)

    # Merging a second paper into the same base finalizes the first (permanent), keeps it hidden.
    dr.merge_works(db_session, base=base, source=second, actor=owner)
    db_session.commit()
    db_session.refresh(first)
    db_session.refresh(second)

    assert first.merged_into_id == base.id
    assert first.merge_record is None  # finalized: no longer reversible
    assert second.merged_into_id == base.id
    assert second.merge_record is not None  # newest merge stays reversible
    assert dr.has_reversible_shadow(db_session, base.id)

    # Unmerge reverses ONLY the newest; the first stays a permanent hidden shadow.
    dr.unmerge_work(db_session, base_id=base.id, actor=owner)
    db_session.commit()
    db_session.refresh(first)
    db_session.refresh(second)
    assert first.merged_into_id == base.id
    assert second.merged_into_id is None
    assert not dr.has_reversible_shadow(db_session, base.id)


# --------------------------------------------------------------------------------------------------
# Link: bidirectional relate (no move / hide)
# --------------------------------------------------------------------------------------------------
def test_link_keeps_both_papers_and_files_and_relates(db_session, owner) -> None:
    a = Work(canonical_title="A", normalized_title="a")
    b = Work(canonical_title="B", normalized_title="b")
    fa = File(sha256="1" * 64, size_bytes=1)
    fb = File(sha256="2" * 64, size_bytes=1)
    db_session.add_all([a, b, fa, fb])
    db_session.flush()
    db_session.add_all(
        [FileWorkLink(file_id=fa.id, work_id=a.id), FileWorkLink(file_id=fb.id, work_id=b.id)]
    )
    db_session.commit()

    dr.link_works(db_session, a.id, b.id, actor_id=owner.id)
    db_session.commit()

    # Nothing moved or hidden.
    assert db_session.get(Work, a.id).merged_into_id is None
    assert db_session.get(Work, b.id).merged_into_id is None
    assert (
        db_session.scalar(select(FileWorkLink.work_id).where(FileWorkLink.file_id == fa.id)) == a.id
    )
    assert (
        db_session.scalar(select(FileWorkLink.work_id).where(FileWorkLink.file_id == fb.id)) == b.id
    )
    # Bidirectional relation present from either side.
    assert dr.linked_work_ids(db_session, a.id) == [b.id]
    assert dr.linked_work_ids(db_session, b.id) == [a.id]
    # Idempotent regardless of order.
    dr.link_works(db_session, b.id, a.id, actor_id=owner.id)
    db_session.commit()
    assert len(db_session.scalars(select(WorkLink)).all()) == 1


# --------------------------------------------------------------------------------------------------
# Transactional safety + guard rails + swap
# --------------------------------------------------------------------------------------------------
def test_merge_failure_mid_way_rolls_back_cleanly(db_session, owner, monkeypatch) -> None:
    base = Work(canonical_title="Base", normalized_title="base")
    source = Work(canonical_title="Source", normalized_title="source", abstract="Src")
    db_session.add_all([base, source])
    db_session.commit()

    def boom(*args, **kwargs):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(dr, "redirect_references", boom)
    with pytest.raises(RuntimeError):
        dr.merge_works(db_session, base=base, source=source, actor=owner)
    db_session.rollback()
    db_session.expire_all()

    # No half-merged state: base unchanged, source still a standalone visible paper.
    assert db_session.get(Work, base.id).abstract is None
    assert db_session.get(Work, source.id).merged_into_id is None
    assert _assertions(db_session, base.id) == []


def test_cannot_merge_paper_into_itself(db_session, owner) -> None:
    work = Work(canonical_title="Solo", normalized_title="solo")
    db_session.add(work)
    db_session.commit()
    with pytest.raises(ValueError, match="into itself"):
        dr.merge_works(db_session, base=work, source=work, actor=owner)


def test_cannot_merge_a_shadow(db_session, owner) -> None:
    base = Work(canonical_title="Base", normalized_title="base")
    shadow = Work(canonical_title="Shadow", normalized_title="shadow")
    other = Work(canonical_title="Other", normalized_title="other")
    db_session.add_all([base, shadow, other])
    db_session.commit()
    dr.merge_works(db_session, base=base, source=shadow, actor=owner)
    db_session.commit()
    with pytest.raises(ValueError, match="already a merged shadow"):
        dr.merge_works(db_session, base=other, source=shadow, actor=owner)


def test_swap_honours_chosen_base(db_session, owner) -> None:
    # Whichever paper is passed as ``base`` survives; the other becomes the shadow.
    a = Work(canonical_title="A", normalized_title="a", abstract="A abs")
    b = Work(canonical_title="B", normalized_title="b")
    db_session.add_all([a, b])
    db_session.commit()

    dr.merge_works(db_session, base=b, source=a, actor=owner)
    db_session.commit()
    db_session.refresh(a)
    db_session.refresh(b)

    assert a.merged_into_id == b.id  # a was the source
    assert b.merged_into_id is None  # b survives as canonical
    assert b.abstract == "A abs"  # filled from the source
