"""BM25F+ lexical engine tests (HS4): field weighting, BM25+ behavior, cache, API wiring."""

import os
import uuid
from pathlib import Path

import pytest
from app.db.base import Base
from app.models.chunk import WorkChunk
from app.models.citation import RawTeiDocument
from app.models.work import Work
from app.services.bm25_index import (
    _label_to_field,
    build_index,
    get_index,
    invalidate_cache,
    lexical_search_papers,
    load_index,
    save_index,
    tokenize,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

TEI_METHODS = """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body>
<div><head>Methods</head><p>We introduce zznovelterm as the core mechanism.</p></div>
</body></text></TEI>"""

TEI_INTRO = """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body>
<div><head>Introduction</head><p>Prior work mentions zznovelterm in passing only.</p></div>
</body></text></TEI>"""


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'bm25.db'}")
    Base.metadata.create_all(
        bind=engine,
        tables=[Work.__table__, WorkChunk.__table__, RawTeiDocument.__table__],
    )
    with sessionmaker(bind=engine, autocommit=False, autoflush=False)() as session:
        yield session


# --- primitives -------------------------------------------------------------


def test_tokenize_drops_stopwords_and_lowercases() -> None:
    tokens = tokenize("The Quick brown Fox and the lazy dog")
    assert "the" not in tokens and "and" not in tokens
    assert "quick" in tokens and "fox" in tokens


def test_label_to_field_routing() -> None:
    assert _label_to_field("title") == "title"
    assert _label_to_field("abstract") == "abstract"
    assert _label_to_field("Introduction") == "body_low"
    assert _label_to_field("Related Work") == "body_low"
    assert _label_to_field("Methods") == "body_high"
    assert _label_to_field(None) == "body_high"


# --- field weighting (the "F") ----------------------------------------------


def test_title_match_outranks_abstract_match(db_session) -> None:
    a = Work(
        canonical_title="photosynthesis pathways",
        normalized_title="a",
        abstract="general plant biology overview",
    )
    b = Work(
        canonical_title="general plant biology overview",
        normalized_title="b",
        abstract="photosynthesis pathways discussed here",
    )
    db_session.add_all([a, b])
    db_session.commit()
    hits = build_index(db_session).search("photosynthesis", limit=5)
    assert hits
    assert hits[0][0] == str(a.id)  # title field (weight 3) beats abstract (weight 2)


def test_methods_section_outranks_intro_section(db_session) -> None:
    """A term in a high-value body section (Methods) beats the same term in intro/related work."""
    methods = Work(canonical_title="paper c", normalized_title="c")
    intro = Work(canonical_title="paper d", normalized_title="d")
    db_session.add_all([methods, intro])
    db_session.flush()
    db_session.add(RawTeiDocument(file_id=uuid.uuid4(), work_id=methods.id, tei_xml=TEI_METHODS))
    db_session.add(RawTeiDocument(file_id=uuid.uuid4(), work_id=intro.id, tei_xml=TEI_INTRO))
    db_session.commit()
    hits = build_index(db_session).search("zznovelterm", limit=5)
    assert hits
    assert hits[0][0] == str(methods.id)  # body_high (1.5) beats body_low (0.5)


def test_search_returns_only_matching_docs(db_session) -> None:
    a = Work(canonical_title="quantum entanglement", normalized_title="a", abstract="physics")
    b = Work(canonical_title="sourdough bread", normalized_title="b", abstract="baking")
    db_session.add_all([a, b])
    db_session.commit()
    hits = build_index(db_session).search("quantum", limit=5)
    assert [h[0] for h in hits] == [str(a.id)]  # only the matching doc, positive score
    assert hits[0][1] > 0


# --- manager: caching, invalidation, visibility, PaperHit -------------------


def test_get_index_caches_and_rebuilds_on_change(db_session) -> None:
    invalidate_cache()
    db_session.add(Work(canonical_title="alpha", normalized_title="alpha"))
    db_session.commit()
    first = get_index(db_session)
    assert get_index(db_session) is first  # cached (signature unchanged)
    db_session.add(Work(canonical_title="beta", normalized_title="beta"))
    db_session.commit()
    rebuilt = get_index(db_session)
    assert rebuilt is not first
    assert len(rebuilt.work_ids) == len(first.work_ids) + 1


def test_save_index_prunes_superseded_signatures(db_session, tmp_path: Path) -> None:
    db_session.add(Work(canonical_title="alpha", normalized_title="alpha"))
    db_session.commit()
    directory = str(tmp_path / "index")

    old = build_index(db_session)
    old.signature, old.key = "sig-old", "oldkey"
    save_index(old, directory)
    assert any(name.startswith("bm25-oldkey.") for name in os.listdir(directory))

    new = build_index(db_session)
    new.signature, new.key = "sig-new", "newkey"
    save_index(new, directory)
    names = os.listdir(directory)
    assert not any(name.startswith("bm25-oldkey.") for name in names)
    assert load_index(directory, "newkey", "sig-new") is not None


def test_lexical_search_papers_ranks_and_filters_visible(db_session) -> None:
    invalidate_cache()
    a = Work(canonical_title="photosynthesis pathways", normalized_title="a")
    b = Work(canonical_title="photosynthesis in algae", normalized_title="b")
    db_session.add_all([a, b])
    db_session.commit()
    hits = lexical_search_papers(db_session, "photosynthesis", visible_ids=None, limit=5)
    assert {h.work.id for h in hits} == {a.id, b.id}
    only_b = lexical_search_papers(db_session, "photosynthesis", visible_ids={b.id}, limit=5)
    assert [h.work.id for h in only_b] == [b.id]


# --- API --------------------------------------------------------------------


def test_warm_endpoint_builds_index(client, auth_headers, db) -> None:
    db.add(Work(canonical_title="Quantum entanglement", normalized_title="q", abstract="physics"))
    db.commit()
    r = client.post("/api/v1/search/warm", headers=auth_headers("reader"))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["lexical_indexed_docs"] >= 1


def test_lexical_mode_api_uses_bm25f(client, auth_headers, db) -> None:
    db.add(
        Work(
            canonical_title="Quantum entanglement networks",
            normalized_title="q",
            abstract="spooky action at a distance",
        )
    )
    db.commit()
    r = client.post(
        "/api/v1/search/semantic",
        headers=auth_headers("reader"),
        json={"q": "quantum entanglement", "mode": "lexical"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["items"]
    assert body["items"][0]["title"] == "Quantum entanglement networks"
    assert body["embedding_provider_used"] is None
    assert body["degraded"] is False
