"""The Library filter query builder (S4: moved out of the works endpoint module).

``build_works_query`` is the ONE query producing "the papers this user sees under these filters":
the Library page, saved filters, graph/export/visualization scope resolution all compose on it.
It lived in the works *endpoint* module, which forced services (``saved_filters``) to import from
the HTTP layer — an inverted dependency. It now lives here; the endpoint imports it like any
other service helper.
"""

from __future__ import annotations

import contextlib
import uuid

from sqlalchemy import Select, String, and_, cast, func, or_, select
from sqlalchemy.orm import Session

from app.models.ai import Summary
from app.models.annotation import Annotation
from app.models.chunk import WorkChunk
from app.models.citation import RawTeiDocument, Reference, ReferenceCitation
from app.models.duplicate import DuplicateCandidate
from app.models.file import File, FileWorkLink
from app.models.metadata import MetadataAssertion
from app.models.organization import RackShelf, RowRack, ShelfWork, Tag, TagLink
from app.models.user import User
from app.models.work import Work, WorkVersion
from app.services import access
from app.services.search_query import parse_search_query
from app.utils.normalization import normalize_doi


def _looks_like_hash(text: str) -> bool:
    """True if ``text`` could be a sha256 or a sha256 prefix (all hex, 8..64 chars).

    The lower bound (8) avoids treating short common words like "deed" or "cafe" as hashes.
    """
    candidate = text.strip()
    return 8 <= len(candidate) <= 64 and all(c in "0123456789abcdefABCDEF" for c in candidate)


def _name_or_id_condition(name_col, id_col, value: str):
    """Build a SAFE ``name ILIKE %value% [OR id == value]`` predicate for a name-or-id operator.

    ``value`` is always bound through the ORM (never interpolated). When it parses as a UUID we also
    match the id column exactly, so ``shelf:<uuid>`` / ``rack:<uuid>`` / ``cites:<uuid>`` work; a
    non-UUID string matches by name only.
    """
    conditions = [name_col.ilike(f"%{value}%")]
    with contextlib.suppress(ValueError):
        conditions.append(id_col == uuid.UUID(value))
    return or_(*conditions)


# Work columns that the `missing` filter can test for absence (NULL or empty string).
_MISSING_FIELDS = {
    "title": Work.canonical_title,
    "abstract": Work.abstract,
    "year": Work.year,
    "venue": Work.venue,
    "doi": Work.doi,
    "arxiv_id": Work.arxiv_id,
}


def build_works_query(
    db: Session,
    actor: User,
    *,
    q: str | None = None,
    reading_status: str | None = None,
    shelf_id: uuid.UUID | None = None,
    rack_id: uuid.UUID | None = None,
    row_id: uuid.UUID | None = None,
    tag_id: uuid.UUID | None = None,
    tag_any: list[uuid.UUID] | None = None,
    tag_all: list[uuid.UUID] | None = None,
    tag_none: list[uuid.UUID] | None = None,
    has_pdf: bool | None = None,
    has_references: bool | None = None,
    missing: str | None = None,
) -> Select:
    """Build the ``select(Work)`` for the Library filter, WITHOUT sort/limit.

    Starts from ``access.visible_works_query`` — the visibility floor — so every condition composes
    on top of the caller's see-able set and the query can never widen beyond it. ``list_works``
    calls this then appends sort/limit; saved-filter/graph/export resolvers reuse it to produce the
    exact same work set (auto visibility-clamped). ``distinct()`` is applied so the shelf/rack/tag
    joins don't duplicate rows. See ``list_works`` for the operator/param semantics.
    """
    parsed = parse_search_query(q)
    stmt = access.visible_works_query(db, actor)
    if parsed.text:
        like = f"%{parsed.text}%"
        conditions = [
            Work.canonical_title.ilike(like),
            Work.abstract.ilike(like),
            Work.doi.ilike(like),
            Work.arxiv_id.ilike(like),
            Work.venue.ilike(like),
        ]
        # A query that looks like a sha256 (full or a hex prefix) also matches the paper that owns
        # a file with that content hash — so pasting the hash shown in the file row finds the paper.
        if _looks_like_hash(parsed.text):
            owns_hash = (
                select(FileWorkLink.work_id)
                .join(File, File.id == FileWorkLink.file_id)
                .where(
                    FileWorkLink.work_id == Work.id,
                    File.sha256.ilike(f"{parsed.text.lower()}%"),
                )
                .exists()
            )
            conditions.append(owns_hash)
        stmt = stmt.where(or_(*conditions))
    if parsed.title:
        stmt = stmt.where(Work.canonical_title.ilike(f"%{parsed.title}%"))
    if parsed.venue:
        stmt = stmt.where(Work.venue.ilike(f"%{parsed.venue}%"))
    if parsed.work_type:
        stmt = stmt.where(Work.work_type == parsed.work_type)
    if parsed.keyword:
        # keywords/topics are JSON lists; a lowercased text cast + LIKE matches on both Postgres
        # (JSONB) and SQLite (JSON-as-text) without a per-dialect containment operator.
        stmt = stmt.where(
            func.lower(cast(Work.keywords, String)).like(f"%{parsed.keyword.lower()}%")
        )
    if parsed.topic:
        stmt = stmt.where(func.lower(cast(Work.topics, String)).like(f"%{parsed.topic.lower()}%"))
    if parsed.year_min is not None:
        stmt = stmt.where(Work.year >= parsed.year_min)
    if parsed.year_max is not None:
        stmt = stmt.where(Work.year <= parsed.year_max)
    if parsed.author:
        author_match = (
            select(MetadataAssertion.id)
            .where(
                MetadataAssertion.entity_type == "work",
                MetadataAssertion.entity_id == Work.id,
                MetadataAssertion.field_name == "authors",
                MetadataAssertion.value.ilike(f"%{parsed.author}%"),
            )
            .exists()
        )
        stmt = stmt.where(author_match)
    if parsed.tag:
        stmt = stmt.where(
            select(Tag.id)
            .join(TagLink, TagLink.tag_id == Tag.id)
            .where(
                TagLink.entity_type == "work",
                TagLink.entity_id == Work.id,
                Tag.name.ilike(f"%{parsed.tag}%"),
            )
            .exists()
        )
    if parsed.doi:
        # Match the stored (already normalized) DOI as a normalized prefix so ``doi:10.1/x`` finds a
        # paper whose DOI was stored with a scheme/prefix; a full DOI is an exact prefix match.
        stmt = stmt.where(Work.doi.ilike(f"{normalize_doi(parsed.doi)}%"))
    if parsed.arxiv:
        # arXiv ids may be stored versioned (arxiv_id) or base (arxiv_base_id); match either.
        arxiv_like = f"%{parsed.arxiv}%"
        stmt = stmt.where(
            or_(Work.arxiv_id.ilike(arxiv_like), Work.arxiv_base_id.ilike(arxiv_like))
        )
    if parsed.reading_status:
        stmt = stmt.where(Work.reading_status == parsed.reading_status)
    if parsed.shelf:
        # Works in a shelf matched by name or id, restricted to shelves the caller may SEE (never
        # widens visibility: a private shelf without a grant contributes no works).
        shelf_ids = access.visible_shelves_query(db, actor).subquery()
        shelf_cond = _name_or_id_condition(shelf_ids.c.name, shelf_ids.c.id, parsed.shelf)
        member = (
            select(ShelfWork.work_id)
            .join(shelf_ids, shelf_ids.c.id == ShelfWork.shelf_id)
            .where(ShelfWork.work_id == Work.id, shelf_cond)
            .exists()
        )
        stmt = stmt.where(member)
    if parsed.rack:
        # Works whose shelf sits in a rack matched by name or id, restricted to racks the caller may
        # SEE (rack->shelf->work; only see-able racks contribute).
        rack_ids = access.visible_racks_query(db, actor).subquery()
        rack_cond = _name_or_id_condition(rack_ids.c.name, rack_ids.c.id, parsed.rack)
        member = (
            select(ShelfWork.work_id)
            .join(RackShelf, RackShelf.shelf_id == ShelfWork.shelf_id)
            .join(rack_ids, rack_ids.c.id == RackShelf.rack_id)
            .where(ShelfWork.work_id == Work.id, rack_cond)
            .exists()
        )
        stmt = stmt.where(member)
    if parsed.row:
        # Works whose shelf sits in a rack that sits in a row matched by name or id, restricted to
        # rows the caller may SEE (row->rack->shelf->work; only see-able rows contribute).
        row_ids_q = access.visible_rows_query(db, actor).subquery()
        row_cond = _name_or_id_condition(row_ids_q.c.name, row_ids_q.c.id, parsed.row)
        member = (
            select(ShelfWork.work_id)
            .join(RackShelf, RackShelf.shelf_id == ShelfWork.shelf_id)
            .join(RowRack, RowRack.rack_id == RackShelf.rack_id)
            .join(row_ids_q, row_ids_q.c.id == RowRack.row_id)
            .where(ShelfWork.work_id == Work.id, row_cond)
            .exists()
        )
        stmt = stmt.where(member)
    if parsed.cites:
        # cites:X — works that cite the work(s) matching X. A local citation edge is a Reference
        # resolved to a target work, cited by a work via ReferenceCitation, so this keeps works that
        # have a citation onto a resolved reference whose target matches X (title/id).
        target = Work.__table__.alias("cited_target")
        cites_cond = _name_or_id_condition(target.c.canonical_title, target.c.id, parsed.cites)
        edge = (
            select(ReferenceCitation.id)
            .join(Reference, Reference.id == ReferenceCitation.reference_id)
            .join(target, target.c.id == Reference.resolved_work_id)
            .where(ReferenceCitation.citing_work_id == Work.id, cites_cond)
            .exists()
        )
        stmt = stmt.where(edge)
    if parsed.cited_by_local:
        # cited_by_local:X — works cited BY the work(s) matching X (the reverse edge): keep works
        # that are the resolved target of a reference whose citing work matches X.
        source = Work.__table__.alias("citing_source")
        src_cond = _name_or_id_condition(
            source.c.canonical_title, source.c.id, parsed.cited_by_local
        )
        edge = (
            select(ReferenceCitation.id)
            .join(Reference, Reference.id == ReferenceCitation.reference_id)
            .join(source, source.c.id == ReferenceCitation.citing_work_id)
            .where(Reference.resolved_work_id == Work.id, src_cond)
            .exists()
        )
        stmt = stmt.where(edge)
    if parsed.abstract:  # abstract:<text> — match within the abstract column
        stmt = stmt.where(Work.abstract.ilike(f"%{parsed.abstract}%"))
    if parsed.summary:  # summary:<text> — match within a stored summary's text
        stmt = stmt.where(
            select(Summary.id)
            .where(
                Summary.entity_type == "work",
                Summary.entity_id == Work.id,
                Summary.text.ilike(f"%{parsed.summary}%"),
            )
            .exists()
        )
    if parsed.fulltext:  # fulltext:<text> — match within the work's extracted body chunks
        stmt = stmt.where(
            select(WorkChunk.id)
            .where(WorkChunk.work_id == Work.id, WorkChunk.text.ilike(f"%{parsed.fulltext}%"))
            .exists()
        )
    if parsed.file_name:  # file:<name> — match a linked file's original_filename
        stmt = stmt.where(
            select(FileWorkLink.id)
            .join(File, File.id == FileWorkLink.file_id)
            .where(
                FileWorkLink.work_id == Work.id,
                File.original_filename.ilike(f"%{parsed.file_name}%"),
            )
            .exists()
        )
    if parsed.duplicate is not None:
        # duplicate:<yes|no> — the work is entity A or B of an OPEN duplicate candidate.
        open_dup = (
            select(DuplicateCandidate.id)
            .where(
                DuplicateCandidate.status == "open",
                or_(
                    and_(
                        DuplicateCandidate.entity_a_type == "work",
                        DuplicateCandidate.entity_a_id == Work.id,
                    ),
                    and_(
                        DuplicateCandidate.entity_b_type == "work",
                        DuplicateCandidate.entity_b_id == Work.id,
                    ),
                ),
            )
            .exists()
        )
        stmt = stmt.where(open_dup if parsed.duplicate else ~open_dup)
    if parsed.version is not None:
        # version:<yes|no> — the work is part of a version group (a shared version_group_id or a
        # WorkVersion row records a concrete version of it).
        in_group = or_(
            Work.version_group_id.is_not(None),
            select(WorkVersion.id).where(WorkVersion.work_id == Work.id).exists(),
        )
        stmt = stmt.where(in_group if parsed.version else ~in_group)
    if parsed.warning:
        # warning:<text|*> — a linked file carries a review warning (FileWorkLink.warning_state).
        # "*"/"any" matches any non-"none" state; a literal matches a warning_state substring.
        link = select(FileWorkLink.id).where(FileWorkLink.work_id == Work.id)
        if parsed.warning in ("*", "any", "true", "yes"):
            link = link.where(FileWorkLink.warning_state != "none")
        else:
            link = link.where(FileWorkLink.warning_state.ilike(f"%{parsed.warning}%"))
        stmt = stmt.where(link.exists())
    # Operator-derived has:* unless the caller passed explicit query params (those win).
    if has_pdf is None:
        has_pdf = parsed.has_pdf
    if has_references is None:
        has_references = parsed.has_references
    if reading_status:
        stmt = stmt.where(Work.reading_status == reading_status)
    if shelf_id or rack_id or row_id:
        stmt = stmt.join(ShelfWork, ShelfWork.work_id == Work.id)
    if shelf_id:
        stmt = stmt.where(ShelfWork.shelf_id == shelf_id)
    if rack_id or row_id:  # both need the shelf→rack hop; join RackShelf only once
        stmt = stmt.join(RackShelf, RackShelf.shelf_id == ShelfWork.shelf_id)
    if rack_id:
        stmt = stmt.where(RackShelf.rack_id == rack_id)
    if row_id:
        stmt = stmt.join(RowRack, RowRack.rack_id == RackShelf.rack_id).where(
            RowRack.row_id == row_id
        )
    if tag_id:
        stmt = stmt.join(
            TagLink,
            (TagLink.entity_id == Work.id) & (TagLink.entity_type == "work"),
        ).where(TagLink.tag_id == tag_id)

    # Advanced multi-tag filter (per-tag has/must-have/excludes). Uses EXISTS subqueries — not JOINs —
    # so multiple tag conditions never multiply rows (no reliance on distinct) and each is independent:
    #   tag_all  → the paper MUST have every one of these (AND of EXISTS);
    #   tag_none → the paper must have NONE of these (AND of NOT EXISTS) — always strict;
    #   tag_any  → when non-empty, the paper must have AT LEAST ONE (OR of EXISTS); skipped if empty.
    # Applied in that order, so tag_any narrows the set already constrained by tag_all/tag_none.
    def _has_tag(tid: uuid.UUID):
        return (
            select(TagLink.tag_id)
            .where(
                TagLink.entity_type == "work",
                TagLink.entity_id == Work.id,
                TagLink.tag_id == tid,
            )
            .exists()
        )

    for tid in tag_all or ():
        stmt = stmt.where(_has_tag(tid))
    for tid in tag_none or ():
        stmt = stmt.where(~_has_tag(tid))
    if tag_any:
        stmt = stmt.where(or_(*[_has_tag(tid) for tid in tag_any]))
    if has_pdf is not None:
        has_file = select(FileWorkLink.work_id).where(FileWorkLink.work_id == Work.id).exists()
        stmt = stmt.where(has_file if has_pdf else ~has_file)
    if has_references is not None:
        has_refs = (
            select(ReferenceCitation.id).where(ReferenceCitation.citing_work_id == Work.id).exists()
        )
        stmt = stmt.where(has_refs if has_references else ~has_refs)
    if parsed.has_annotations:  # has:notes / has:annotations — ≥1 annotation on the work
        stmt = stmt.where(select(Annotation.id).where(Annotation.work_id == Work.id).exists())
    if parsed.has_summary:  # has:summary — ≥1 stored summary for the work
        stmt = stmt.where(
            select(Summary.id)
            .where(Summary.entity_type == "work", Summary.entity_id == Work.id)
            .exists()
        )
    if parsed.has_abstract:  # has:abstract — a non-empty abstract column
        stmt = stmt.where(Work.abstract.is_not(None), Work.abstract != "")
    if parsed.has_grobid:  # has:grobid — a GROBID TEI document was extracted for the work
        stmt = stmt.where(
            select(RawTeiDocument.id).where(RawTeiDocument.work_id == Work.id).exists()
        )
    if parsed.has_ocr:  # has:ocr — a linked file gained an OCR text layer (text_layer_quality)
        stmt = stmt.where(
            select(FileWorkLink.id)
            .join(File, File.id == FileWorkLink.file_id)
            .where(FileWorkLink.work_id == Work.id, File.text_layer_quality == "ocr_added")
            .exists()
        )
    for field in (missing or "").split(","):
        name = field.strip()
        column = _MISSING_FIELDS.get(name)
        if column is None:
            continue
        # Non-text columns (year) only test NULL; text columns also treat "" as missing.
        stmt = stmt.where(
            column.is_(None) if name == "year" else or_(column.is_(None), column == "")
        )
    return stmt.distinct()
