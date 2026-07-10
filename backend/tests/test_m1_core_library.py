"""M1 core library service tests."""

from pathlib import Path

import pytest
from app.api.v1.endpoints.files import list_files, stream_file
from app.api.v1.endpoints.racks import (
    RackUpdate,
    list_rack_shelves,
    remove_shelf_from_rack,
    update_rack,
)
from app.api.v1.endpoints.shelves import (
    ShelfUpdate,
    list_shelf_works,
    remove_work_from_shelf,
    update_shelf,
)
from app.api.v1.endpoints.tags import remove_tag_link
from app.api.v1.endpoints.works import list_works
from app.core.config import Settings
from app.core.security import hash_password
from app.db.base import Base
from app.models.audit import AuditEvent
from app.models.citation import Reference
from app.models.file import File, FileWorkLink, Location
from app.models.metadata import MetadataAssertion
from app.models.organization import Rack, RackShelf, Shelf, ShelfWork, Tag, TagLink
from app.models.source import ImportBatch, Source
from app.models.user import User
from app.models.work import Work
from app.services.storage import (
    configured_server_roots,
    create_server_folder_source,
    import_server_folder,
)
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'm1.db'}")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            User.__table__,
            AuditEvent.__table__,
            Source.__table__,
            ImportBatch.__table__,
            File.__table__,
            Location.__table__,
            Work.__table__,
            FileWorkLink.__table__,
            Shelf.__table__,
            Rack.__table__,
            ShelfWork.__table__,
            RackShelf.__table__,
            Tag.__table__,
            TagLink.__table__,
            Reference.__table__,
            # list_works reads metadata_assertions for the conflict badge (batch10 issue 5).
            MetadataAssertion.__table__,
        ],
    )
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with session_local() as session:
        yield session


@pytest.fixture()
def owner(db_session) -> User:
    user = User(username="owner", password_hash=hash_password("secret"), role="owner")
    db_session.add(user)
    db_session.commit()
    return user


def test_configured_server_roots_support_aliases(tmp_path: Path) -> None:
    papers = tmp_path / "papers"
    papers.mkdir()
    settings = Settings(server_allowed_roots=[{"alias": "main", "path": str(papers)}])

    assert configured_server_roots(settings) == {"main": papers.resolve()}


def test_server_folder_source_requires_configured_alias(
    db_session,
    owner: User,
    tmp_path: Path,
) -> None:
    settings = Settings(server_allowed_roots=[{"alias": "main", "path": str(tmp_path)}])

    with pytest.raises(ValueError, match="Unknown server-folder alias"):
        create_server_folder_source(
            db_session,
            settings=settings,
            name="Bad",
            path_alias="other",
            actor=owner,
        )


def test_import_server_folder_creates_file_work_location_batch_and_audit(
    db_session,
    owner: User,
    tmp_path: Path,
) -> None:
    papers = tmp_path / "papers"
    papers.mkdir()
    (papers / "A Useful Paper.pdf").write_bytes(b"%PDF-1.4\n% test fixture\n")
    settings = Settings(server_allowed_roots=[{"alias": "main", "path": str(papers)}])
    source = create_server_folder_source(
        db_session,
        settings=settings,
        name="Main papers",
        path_alias="main",
        actor=owner,
    )
    db_session.commit()

    batch = import_server_folder(db_session, source=source, actor=owner)
    db_session.commit()

    assert batch.status == "completed"
    assert batch.stats == {
        "seen": 1,
        "created_files": 1,
        "created_works": 1,
        "existing_files": 0,
        "errors": 0,
    }
    file = db_session.scalar(select(File))
    assert file.original_filename == "A Useful Paper.pdf"
    assert file.mime_type == "application/pdf"
    assert db_session.scalar(select(Location)).source_id == source.id
    work = db_session.scalar(select(Work))
    assert work.canonical_title == "A Useful Paper"
    assert db_session.scalar(select(FileWorkLink)).work_id == work.id
    assert db_session.scalars(
        select(AuditEvent).where(AuditEvent.event_type == "import.folder_completed")
    ).all()

    second_batch = import_server_folder(db_session, source=source, actor=owner)
    db_session.commit()

    assert second_batch.stats["created_files"] == 0
    assert second_batch.stats["existing_files"] == 1
    assert db_session.scalar(select(func.count()).select_from(File)) == 1


def test_import_server_folder_isolates_a_bad_file_and_commits_the_rest(
    db_session, owner: User, tmp_path: Path, monkeypatch
) -> None:
    """D9: one unreadable file is skipped (recorded in ``errors``); the good files still import."""
    from app.services import storage

    papers = tmp_path / "papers"
    papers.mkdir()
    (papers / "good1.pdf").write_bytes(b"%PDF-1.4\n% good one\n")
    (papers / "bad.pdf").write_bytes(b"%PDF-1.4\n% will explode\n")
    (papers / "good2.pdf").write_bytes(b"%PDF-1.4\n% good two\n")
    settings = Settings(server_allowed_roots=[{"alias": "main", "path": str(papers)}])
    source = create_server_folder_source(
        db_session, settings=settings, name="Main", path_alias="main", actor=owner
    )
    db_session.commit()

    real = storage._sha256_file

    def boom_on_bad(path):
        if path.name == "bad.pdf":
            raise OSError("simulated unreadable file")
        return real(path)

    monkeypatch.setattr(storage, "_sha256_file", boom_on_bad)
    batch = import_server_folder(db_session, source=source, actor=owner)

    assert batch.status == "completed"  # not "failed": the batch as a whole succeeded
    assert batch.stats["seen"] == 3
    assert batch.stats["created_files"] == 2  # both good files imported
    assert batch.stats["errors"] == 1  # the bad file is recorded, not fatal
    # The two good files are durably committed (partial import is visible).
    assert db_session.scalar(select(func.count()).select_from(File)) == 2


def test_import_server_folder_commits_batch_row_up_front(
    db_session, owner: User, tmp_path: Path
) -> None:
    """D9: the batch row is persisted before the scan finishes (survives a later crash)."""
    from app.models.source import ImportBatch

    papers = tmp_path / "papers"
    papers.mkdir()
    (papers / "p.pdf").write_bytes(b"%PDF-1.4\n% fixture\n")
    settings = Settings(server_allowed_roots=[{"alias": "main", "path": str(papers)}])
    source = create_server_folder_source(
        db_session, settings=settings, name="Main", path_alias="main", actor=owner
    )
    db_session.commit()

    batch = import_server_folder(db_session, source=source, actor=owner)
    # A separate session sees the finalized batch without the caller committing again.
    other = sessionmaker(bind=db_session.get_bind(), autoflush=False)()
    try:
        persisted = other.get(ImportBatch, batch.id)
        assert persisted is not None
        assert persisted.status == "completed"
    finally:
        other.close()


def test_import_server_folder_skips_rehash_for_unchanged_files(
    db_session, owner: User, tmp_path: Path, monkeypatch
) -> None:
    """A re-scan of an unchanged folder does not re-hash files (E7 incremental scan)."""
    from app.services import storage

    papers = tmp_path / "papers"
    papers.mkdir()
    (papers / "p.pdf").write_bytes(b"%PDF-1.4\n% fixture\n")
    settings = Settings(server_allowed_roots=[{"alias": "main", "path": str(papers)}])
    source = create_server_folder_source(
        db_session, settings=settings, name="Main", path_alias="main", actor=owner
    )
    db_session.commit()
    import_server_folder(
        db_session, source=source, actor=owner
    )  # first scan hashes + records mtime
    db_session.commit()

    calls = {"n": 0}
    real = storage._sha256_file

    def counting(path):
        calls["n"] += 1
        return real(path)

    monkeypatch.setattr(storage, "_sha256_file", counting)
    import_server_folder(db_session, source=source, actor=owner)  # unchanged → no re-hash
    db_session.commit()
    assert calls["n"] == 0


def test_m1_read_endpoints_return_file_shelf_and_rack_memberships(db_session, owner: User) -> None:
    file = File(
        sha256="a" * 64,
        size_bytes=123,
        mime_type="application/pdf",
        original_filename="paper.pdf",
    )
    work = Work(canonical_title="Paper", normalized_title="paper")
    shelf = Shelf(name="Important", created_by_user_id=owner.id)
    rack = Rack(name="Thesis", created_by_user_id=owner.id)
    db_session.add_all([file, work, shelf, rack])
    db_session.flush()
    db_session.add_all(
        [
            ShelfWork(shelf_id=shelf.id, work_id=work.id, added_by_user_id=owner.id),
            RackShelf(rack_id=rack.id, shelf_id=shelf.id, added_by_user_id=owner.id),
        ]
    )
    db_session.commit()

    assert list_files(limit=100, db=db_session, actor=owner)[0].id == file.id
    assert list_shelf_works(shelf.id, db=db_session, actor=owner)[0].id == work.id
    assert list_rack_shelves(rack.id, db=db_session, actor=owner)[0].id == shelf.id


def test_work_list_filters_by_shelf_rack_and_tag(db_session, owner: User) -> None:
    included = Work(canonical_title="Included", normalized_title="included")
    excluded = Work(canonical_title="Excluded", normalized_title="excluded")
    shelf = Shelf(name="Important", created_by_user_id=owner.id)
    rack = Rack(name="Thesis", created_by_user_id=owner.id)
    tag = Tag(name="Methods", normalized_name="methods")
    db_session.add_all([included, excluded, shelf, rack, tag])
    db_session.flush()
    db_session.add_all(
        [
            ShelfWork(shelf_id=shelf.id, work_id=included.id, added_by_user_id=owner.id),
            RackShelf(rack_id=rack.id, shelf_id=shelf.id, added_by_user_id=owner.id),
            TagLink(
                tag_id=tag.id,
                entity_type="work",
                entity_id=included.id,
                created_by_user_id=owner.id,
            ),
        ]
    )
    db_session.commit()

    def filtered_ids(**filters) -> list:
        return [
            work.id
            for work in list_works(
                q=None,
                reading_status=None,
                shelf_id=filters.get("shelf_id"),
                rack_id=filters.get("rack_id"),
                tag_id=filters.get("tag_id"),
                page=1,
                per_page=100,
                db=db_session,
                actor=owner,
            ).items
        ]

    assert filtered_ids(shelf_id=shelf.id) == [included.id]
    assert filtered_ids(rack_id=rack.id) == [included.id]
    assert filtered_ids(tag_id=tag.id) == [included.id]


def test_work_list_filters_by_extraction_status(db_session, owner: User) -> None:
    """has_pdf / has_references / missing filter on extraction + metadata completeness."""
    import uuid

    # with_pdf: has a file + references; without: bare manual work missing year + abstract.
    with_pdf = Work(canonical_title="Has PDF", normalized_title="has pdf", year=2020, abstract="a")
    without = Work(canonical_title="Bare", normalized_title="bare")
    db_session.add_all([with_pdf, without])
    db_session.flush()
    pdf = File(sha256="c" * 64, size_bytes=1, original_filename="p.pdf")
    db_session.add(pdf)
    db_session.flush()
    db_session.add(FileWorkLink(file_id=pdf.id, work_id=with_pdf.id))
    db_session.add(Reference(id=uuid.uuid4(), citing_work_id=with_pdf.id, raw_citation="ref"))
    db_session.commit()

    def ids(**filters) -> set:
        return {
            w.id
            for w in list_works(
                q=None,
                reading_status=None,
                shelf_id=None,
                rack_id=None,
                tag_id=None,
                page=1,
                per_page=100,
                db=db_session,
                actor=owner,
                **filters,
            ).items
        }

    assert ids(has_pdf=True) == {with_pdf.id}
    assert ids(has_pdf=False) == {without.id}
    assert ids(has_references=True) == {with_pdf.id}
    assert ids(has_references=False) == {without.id}
    assert ids(missing="year,abstract") == {without.id}
    assert ids(missing="title") == set()  # both have titles


def test_stream_file_uses_configured_server_folder_location(
    db_session,
    owner: User,
    tmp_path: Path,
) -> None:
    root = tmp_path / "papers"
    root.mkdir()
    pdf_path = root / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    source = Source(
        type="server_folder",
        name="Papers",
        owner_user_id=owner.id,
        path_alias="papers",
        config={"root_path": str(root)},
    )
    file = File(
        sha256="b" * 64,
        size_bytes=pdf_path.stat().st_size,
        mime_type="application/pdf",
        original_filename="paper.pdf",
    )
    db_session.add_all([source, file])
    db_session.flush()
    db_session.add(
        Location(
            file_id=file.id,
            source_id=source.id,
            location_type="server_path",
            internal_uri=str(pdf_path),
            is_available=True,
        )
    )
    db_session.commit()

    response = stream_file(file.id, db=db_session, actor=owner)

    assert Path(response.path) == pdf_path
    assert response.media_type == "application/pdf"


def test_stream_file_rejects_location_outside_configured_root(
    db_session,
    owner: User,
    tmp_path: Path,
) -> None:
    root = tmp_path / "papers"
    root.mkdir()
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF-1.4\n")
    source = Source(
        type="server_folder",
        name="Papers",
        owner_user_id=owner.id,
        path_alias="papers",
        config={"root_path": str(root)},
    )
    file = File(sha256="c" * 64, size_bytes=outside.stat().st_size, mime_type="application/pdf")
    db_session.add_all([source, file])
    db_session.flush()
    db_session.add(
        Location(
            file_id=file.id,
            source_id=source.id,
            location_type="server_path",
            internal_uri=str(outside),
            is_available=True,
        )
    )
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        stream_file(file.id, db=db_session, actor=owner)
    assert exc_info.value.status_code == 403


def test_m1_archive_and_unlink_endpoints(db_session, owner: User) -> None:
    work = Work(canonical_title="Paper", normalized_title="paper")
    shelf = Shelf(name="Shelf", created_by_user_id=owner.id)
    rack = Rack(name="Rack", created_by_user_id=owner.id)
    tag = Tag(name="Tag", normalized_name="tag")
    db_session.add_all([work, shelf, rack, tag])
    db_session.flush()
    db_session.add_all(
        [
            ShelfWork(shelf_id=shelf.id, work_id=work.id, added_by_user_id=owner.id),
            RackShelf(rack_id=rack.id, shelf_id=shelf.id, added_by_user_id=owner.id),
            TagLink(
                tag_id=tag.id,
                entity_type="work",
                entity_id=work.id,
                created_by_user_id=owner.id,
            ),
        ]
    )
    db_session.commit()

    update_shelf(shelf.id, ShelfUpdate(status="archived"), db=db_session, actor=owner)
    update_rack(rack.id, RackUpdate(status="archived"), db=db_session, actor=owner)
    remove_work_from_shelf(shelf.id, work.id, db=db_session, actor=owner)
    remove_shelf_from_rack(rack.id, shelf.id, db=db_session, actor=owner)
    remove_tag_link(tag.id, "work", work.id, db=db_session, actor=owner)

    assert db_session.get(Shelf, shelf.id).status == "archived"
    assert db_session.get(Rack, rack.id).status == "archived"
    assert db_session.get(ShelfWork, {"shelf_id": shelf.id, "work_id": work.id}) is None
    assert db_session.get(RackShelf, {"rack_id": rack.id, "shelf_id": shelf.id}) is None
    assert (
        db_session.get(
            TagLink,
            {"tag_id": tag.id, "entity_type": "work", "entity_id": work.id},
        )
        is None
    )
