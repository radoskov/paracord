"""Batch citation import + shared shelf helper tests (Phase J items 5 + 6).

All network is injected — no real Crossref/OpenAlex/Semantic Scholar/GROBID call. Uses a
self-contained in-memory SQLite schema (every model on ``Base.metadata``).
"""

import uuid

import pytest
from app.core.config import Settings
from app.core.security import hash_password
from app.db.base import Base
from app.errors import NotFoundError, PermissionDeniedError
from app.models.metadata import MetadataAssertion
from app.models.organization import Shelf, ShelfWork
from app.models.user import User
from app.models.work import Work
from app.services import batch_import
from app.services.grobid_client import GrobidUnavailableError
from app.services.shelf_membership import add_work_to_shelf_checked
from app.services.web_find import WebCandidate
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

# Heavier suite: slow per-test schema setup (full Base.metadata create_all on file-backed SQLite)
# — moved to the full tier. Run via `make test-full`/`make ready-full` or `pytest -m slow`.
pytestmark = pytest.mark.slow


@pytest.fixture()
def db(tmp_path):
    import app.models  # noqa: F401  (register every model on Base.metadata)

    engine = create_engine(f"sqlite:///{tmp_path / 'batch.db'}")
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with session_local() as session:
        yield session
    engine.dispose()


def _user(db, role: str = "contributor") -> User:
    user = User(
        username=f"{role}-{uuid.uuid4().hex[:6]}", password_hash=hash_password("x"), role=role
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _shelf(db, *, access_level: str = "open") -> Shelf:
    shelf = Shelf(name=f"shelf-{uuid.uuid4().hex[:6]}", access_level=access_level)
    db.add(shelf)
    db.commit()
    db.refresh(shelf)
    return shelf


class _FakeGrobid:
    def __init__(self, tei: str | None = None, *, down: bool = False) -> None:
        self._tei = tei
        self._down = down
        self.calls: list[list[str]] = []

    def process_citation_list_sync(self, lines: list[str]) -> str:
        self.calls.append(list(lines))
        if self._down:
            raise GrobidUnavailableError("grobid down")
        return self._tei or ""


_TEI_TWO = """<?xml version="1.0"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><back><listBibl>
  <biblStruct>
    <analytic>
      <title level="a">Attention Is All You Need</title>
      <author><persName><forename>Ashish</forename><surname>Vaswani</surname></persName></author>
    </analytic>
    <monogr>
      <title level="j">NeurIPS</title>
      <imprint><date when="2017">2017</date></imprint>
    </monogr>
    <idno type="DOI">10.5555/ATTN</idno>
  </biblStruct>
  <biblStruct>
    <monogr><title>Some unparsed line</title></monogr>
  </biblStruct>
</listBibl></back></text></TEI>"""


# --- tei_parser.parse_citation_list -----------------------------------------


def test_parse_citation_list_extracts_references():
    from app.services.tei_parser import parse_citation_list

    refs = parse_citation_list(_TEI_TWO)
    assert len(refs) == 2
    assert refs[0].title == "Attention Is All You Need"
    assert refs[0].doi == "10.5555/ATTN"
    assert refs[0].year == 2017
    assert refs[0].venue == "NeurIPS"
    assert refs[0].authors == ["Ashish Vaswani"]
    # Empty / malformed TEI returns [].
    assert parse_citation_list("") == []
    assert parse_citation_list("<not-xml") == []


# --- preview: lookup engine -------------------------------------------------


def test_preview_lookup_matched_and_title_only():
    """A confident top candidate => matched + prefilled; a weak/empty line => title_only."""
    settings = Settings()
    p0 = batch_import.preview_lines(
        ["Attention Is All You Need"],
        engine="lookup",
        settings=settings,
        fetchers={
            "crossref": lambda: [
                WebCandidate(
                    source="crossref",
                    title="Attention Is All You Need",
                    authors=["Ashish Vaswani"],
                    year=2017,
                    doi="10.5555/ATTN",
                )
            ],
            "openalex": lambda: [],
            "semanticscholar": lambda: [],
        },
    )
    assert len(p0.drafts) == 1
    d = p0.drafts[0]
    assert d.match_status == "matched"
    assert d.suggested_doi == "10.5555/ATTN"
    assert d.suggested_title == "Attention Is All You Need"
    assert d.suggested_year == 2017
    assert d.candidates and d.candidates[0].confidence >= 0.6

    p1 = batch_import.preview_lines(
        ["A line nothing matches"],
        engine="lookup",
        settings=settings,
        fetchers={
            "crossref": lambda: [],
            "openalex": lambda: [],
            "semanticscholar": lambda: [],
        },
    )
    assert p1.drafts[0].match_status == "title_only"
    assert p1.drafts[0].suggested_title == "A line nothing matches"
    assert p1.drafts[0].suggested_doi is None


def test_preview_lookup_salvages_year_and_doi_when_no_match():
    """The default 'lookup' engine: when no external match is found, an explicit DOI/year in the
    line is still recovered and stripped from the title (owner-reported: default tab left it all in
    the title)."""
    p = batch_import.preview_lines(
        ["Knowledge Graph Embedding via Dynamic Mapping Matrix (2015) doi:10.3115/v1/p15-1067"],
        engine="lookup",
        settings=Settings(),
        fetchers={"crossref": lambda: [], "openalex": lambda: [], "semanticscholar": lambda: []},
    )
    d = p.drafts[0]
    assert d.suggested_year == 2015
    assert d.suggested_doi == "10.3115/v1/p15-1067"
    assert d.suggested_title == "Knowledge Graph Embedding via Dynamic Mapping Matrix"


def test_preview_lookup_degraded_when_budget_exhausted(monkeypatch):
    """A zero wall-clock budget skips every line => title_only + degraded flag."""
    settings = Settings()
    monkeypatch.setattr(settings, "web_find_total_budget", -1.0, raising=False)
    called = {"n": 0}

    def crossref():
        called["n"] += 1
        return []

    preview = batch_import.preview_lines(
        ["one", "two"],
        engine="lookup",
        settings=settings,
        fetchers={"crossref": crossref, "openalex": lambda: [], "semanticscholar": lambda: []},
    )
    assert preview.degraded is True
    assert all(d.match_status == "title_only" for d in preview.drafts)
    assert called["n"] == 0  # budget exhausted before any fetch ran


# --- preview: grobid engine -------------------------------------------------


def test_preview_grobid_parses_citation_list():
    grobid = _FakeGrobid(_TEI_TWO)
    preview = batch_import.preview_lines(
        ["Vaswani et al, Attention Is All You Need, NeurIPS 2017", "Some unparsed line"],
        engine="grobid",
        settings=Settings(),
        grobid=grobid,
    )
    assert grobid.calls == [
        ["Vaswani et al, Attention Is All You Need, NeurIPS 2017", "Some unparsed line"]
    ]
    assert len(preview.drafts) == 2
    first = preview.drafts[0]
    assert first.match_status == "matched"
    assert first.suggested_title == "Attention Is All You Need"
    assert first.suggested_doi == "10.5555/ATTN"
    assert first.suggested_year == 2017
    assert first.suggested_venue == "NeurIPS"
    assert first.suggested_authors == ["Ashish Vaswani"]
    assert preview.grobid_unavailable is False


def test_preview_grobid_unavailable_degrades_all():
    grobid = _FakeGrobid(down=True)
    preview = batch_import.preview_lines(
        ["line a", "line b"], engine="grobid", settings=Settings(), grobid=grobid
    )
    assert preview.grobid_unavailable is True
    assert [d.match_status for d in preview.drafts] == ["title_only", "title_only"]
    assert [d.suggested_title for d in preview.drafts] == ["line a", "line b"]


def test_preview_grobid_salvages_year_and_doi_from_title_string():
    """A 'Title (YYYY) doi:10.…' line: the parser stuffs it all in the title, so we recover the
    year + DOI and strip them out of the title (owner-reported citation-import bug)."""
    # GROBID returns the whole string as the title with no year/doi (the mis-parse we're fixing).
    tei = (
        '<?xml version="1.0"?><TEI xmlns="http://www.tei-c.org/ns/1.0"><text><back><listBibl>'
        '<biblStruct><analytic><title level="a">'
        "SceneGraphFusion: Incremental 3D Scene Graph Prediction from RGB-D Sequences (2021) "
        "doi:10.1109/cvpr46437.2021.00743"
        "</title></analytic></biblStruct>"
        "</listBibl></back></text></TEI>"
    )
    line = (
        "SceneGraphFusion: Incremental 3D Scene Graph Prediction from RGB-D Sequences (2021) "
        "doi:10.1109/cvpr46437.2021.00743"
    )
    preview = batch_import.preview_lines(
        [line], engine="grobid", settings=Settings(), grobid=_FakeGrobid(tei)
    )
    d = preview.drafts[0]
    assert d.suggested_year == 2021
    assert d.suggested_doi == "10.1109/cvpr46437.2021.00743"
    assert d.suggested_title == (
        "SceneGraphFusion: Incremental 3D Scene Graph Prediction from RGB-D Sequences"
    )
    assert d.match_status == "matched"

    # Same recovery when GROBID is unavailable (title_only fallback still salvages the DOI/year).
    down = batch_import.preview_lines(
        ["Knowledge Graph Embedding via Dynamic Mapping Matrix (2015) doi:10.3115/v1/p15-1067"],
        engine="grobid",
        settings=Settings(),
        grobid=_FakeGrobid(down=True),
    )
    d2 = down.drafts[0]
    assert d2.suggested_year == 2015 and d2.suggested_doi == "10.3115/v1/p15-1067"
    assert d2.suggested_title == "Knowledge Graph Embedding via Dynamic Mapping Matrix"


# --- commit -----------------------------------------------------------------


def test_commit_creates_works_with_created_by_and_dedup(db):
    actor = _user(db, "contributor")
    # Pre-seed one existing work so the second draft dedups by DOI.
    existing = Work(canonical_title="Existing", normalized_title="existing", doi="10.1/exists")
    db.add(existing)
    db.commit()

    drafts = [
        batch_import.ConfirmedDraft(
            title="Brand New Paper", authors=["A. One", "B. Two"], year=2020, doi="10.1/new"
        ),
        batch_import.ConfirmedDraft(title="Existing", doi="10.1/exists"),
        batch_import.ConfirmedDraft(title="Skip me", include=False),
    ]
    batch = batch_import.commit_drafts(db, drafts, actor=actor, engine="lookup")
    db.commit()

    assert batch.stats["created"] == 1
    assert batch.stats["matched"] == 1
    assert batch.stats["skipped"] == 1
    assert batch.input_type == "batch_lookup"

    created = db.scalar(select(Work).where(Work.doi == "10.1/new"))
    assert created is not None
    assert created.created_by_user_id == actor.id
    authors = db.scalar(
        select(MetadataAssertion.value).where(
            MetadataAssertion.entity_id == created.id, MetadataAssertion.field_name == "authors"
        )
    )
    assert authors == "A. One; B. Two"


def test_commit_adds_to_shelf_via_checked_helper(db):
    actor = _user(db, "librarian")  # librarian can modify an open shelf
    shelf = _shelf(db, access_level="open")
    drafts = [batch_import.ConfirmedDraft(title="Shelved Paper", doi="10.1/shelf")]
    batch = batch_import.commit_drafts(
        db, drafts, actor=actor, engine="grobid", target_shelf_id=shelf.id
    )
    db.commit()

    assert batch.stats["added_to_shelf"] == 1
    assert batch.stats["target_shelf_id"] == str(shelf.id)
    work = db.scalar(select(Work).where(Work.doi == "10.1/shelf"))
    link = db.get(ShelfWork, {"shelf_id": shelf.id, "work_id": work.id})
    assert link is not None
    assert link.added_by_user_id == actor.id


def test_commit_to_unmodifiable_shelf_403(db):
    actor = _user(db, "contributor")  # contributor cannot modify any shelf structure
    shelf = _shelf(db, access_level="open")
    drafts = [batch_import.ConfirmedDraft(title="Nope", doi="10.1/nope")]
    # S4: the shelf helper raises a framework-free domain error now (mapped to 403 by the app
    # handler for HTTP callers).
    with pytest.raises(PermissionDeniedError) as exc:
        batch_import.commit_drafts(
            db, drafts, actor=actor, engine="lookup", target_shelf_id=shelf.id
        )
    assert exc.value.status_code == 403


# --- shared shelf helper ----------------------------------------------------


def test_add_work_to_shelf_checked_403_without_modify_access(db):
    actor = _user(db, "contributor")
    shelf = _shelf(db, access_level="open")
    work = Work(canonical_title="W", normalized_title="w")
    db.add(work)
    db.commit()
    with pytest.raises(PermissionDeniedError) as exc:
        add_work_to_shelf_checked(db, shelf_id=shelf.id, work_id=work.id, actor=actor)
    assert exc.value.status_code == 403


def test_add_work_to_shelf_checked_404_missing_shelf(db):
    actor = _user(db, "librarian")
    work = Work(canonical_title="W", normalized_title="w")
    db.add(work)
    db.commit()
    with pytest.raises(NotFoundError) as exc:
        add_work_to_shelf_checked(db, shelf_id=uuid.uuid4(), work_id=work.id, actor=actor)
    assert exc.value.status_code == 404


def test_add_work_to_shelf_checked_upserts(db):
    actor = _user(db, "librarian")
    shelf = _shelf(db, access_level="open")
    work = Work(canonical_title="W", normalized_title="w")
    db.add(work)
    db.commit()
    add_work_to_shelf_checked(db, shelf_id=shelf.id, work_id=work.id, actor=actor, position=1)
    db.commit()
    # Re-adding is idempotent (no duplicate row, updates position).
    add_work_to_shelf_checked(db, shelf_id=shelf.id, work_id=work.id, actor=actor, position=5)
    db.commit()
    links = db.scalars(
        select(ShelfWork).where(ShelfWork.shelf_id == shelf.id, ShelfWork.work_id == work.id)
    ).all()
    assert len(links) == 1
    assert links[0].position == 5
