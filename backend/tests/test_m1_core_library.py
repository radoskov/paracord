"""M1 core library service tests."""

from pathlib import Path

import pytest
from app.api.v1.endpoints.files import list_files
from app.api.v1.endpoints.racks import list_rack_shelves
from app.api.v1.endpoints.shelves import list_shelf_works
from app.core.config import Settings
from app.core.security import hash_password
from app.db.base import Base
from app.models.audit import AuditEvent
from app.models.file import File, FileWorkLink, Location
from app.models.organization import Rack, RackShelf, Shelf, ShelfWork
from app.models.source import ImportBatch, Source
from app.models.user import User
from app.models.work import Work
from app.services.storage import (
    configured_server_roots,
    create_server_folder_source,
    import_server_folder,
)
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
    assert batch.stats == {"seen": 1, "created_files": 1, "created_works": 1, "existing_files": 0}
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

    assert list_files(limit=100, db=db_session)[0].id == file.id
    assert list_shelf_works(shelf.id, db=db_session)[0].id == work.id
    assert list_rack_shelves(rack.id, db=db_session)[0].id == shelf.id
