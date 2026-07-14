"""Structured search-query parsing + list_works operators (SPEC §8.7/§14)."""

import uuid

from app.models.ai import Summary
from app.models.annotation import Annotation
from app.models.group import Group, GroupGrant, GroupMembership
from app.models.organization import Rack, RackShelf, Shelf, ShelfWork
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


def test_parses_new_scalar_operators():
    p = parse_search_query(
        'doi:10.1/x arxiv:2101.00001 status:reading shelf:"my shelf" rack:thesis '
        'cites:"Attention" cited_by_local:Survey'
    )
    assert p.doi == "10.1/x"
    assert p.arxiv == "2101.00001"
    assert p.reading_status == "reading"
    assert p.shelf == "my shelf"
    assert p.rack == "thesis"
    assert p.cites == "Attention"
    assert p.cited_by_local == "Survey"
    assert p.text == ""


def test_parses_new_has_values():
    assert parse_search_query("has:notes").has_annotations is True
    assert parse_search_query("has:annotations").has_annotations is True
    assert parse_search_query("has:summary").has_summary is True
    assert parse_search_query("has:abstract").has_abstract is True


def test_existing_has_values_preserved():
    assert parse_search_query("has:pdf").has_pdf is True
    assert parse_search_query("has:file").has_pdf is True
    assert parse_search_query("has:no-pdf").has_pdf is False
    assert parse_search_query("has:references").has_references is True
    # An unrecognized has:* value is still carried as a transparency flag, not an error.
    assert parse_search_query("has:mystery").flags == ["mystery"]


def test_parses_field_scoped_text_operators():
    p = parse_search_query(
        'abstract:transformer summary:"key finding" fulltext:backprop file:paper.pdf'
    )
    assert p.abstract == "transformer"
    assert p.summary == "key finding"
    assert p.fulltext == "backprop"
    assert p.file_name == "paper.pdf"
    assert p.text == ""


def test_parses_extraction_state_has_values():
    assert parse_search_query("has:grobid").has_grobid is True
    assert parse_search_query("has:tei").has_grobid is True
    assert parse_search_query("has:ocr").has_ocr is True


def test_parses_review_state_operators():
    assert parse_search_query("duplicate:yes").duplicate is True
    assert parse_search_query("duplicate:no").duplicate is False
    assert parse_search_query("duplicate:open").duplicate is True
    assert parse_search_query("version:any").version is True
    assert parse_search_query("version:no").version is False
    assert parse_search_query("warning:*").warning == "*"
    assert parse_search_query("warning:multiple").warning == "multiple"


# --- endpoint integration ---------------------------------------------------


def _make(client, headers, **kw):
    return client.post("/api/v1/works", headers=headers, json=kw).json()


def test_list_works_year_and_title_operators(client, auth_headers):
    h = auth_headers("editor")
    _make(client, h, canonical_title="Old transformer paper", year=2015)
    _make(client, h, canonical_title="New transformer paper", year=2022)
    _make(client, h, canonical_title="Unrelated baking", year=2022)

    reader = auth_headers("reader")
    got = client.get("/api/v1/works?q=transformer year:>=2020", headers=reader).json()["items"]
    titles = {w["canonical_title"] for w in got}
    assert titles == {"New transformer paper"}


def test_list_works_has_pdf_operator(client, auth_headers):
    h = auth_headers("editor")
    _make(client, h, canonical_title="No-file paper xyz")
    got = client.get("/api/v1/works?q=xyz has:no-pdf", headers=h).json()["items"]
    assert any(w["canonical_title"] == "No-file paper xyz" for w in got)


def _titles(resp):
    return {w["canonical_title"] for w in resp.json()["items"]}


def test_list_works_doi_and_arxiv_operators(client, auth_headers):
    h = auth_headers("editor")
    _make(client, h, canonical_title="DOI paper", doi="10.1234/abc")
    _make(client, h, canonical_title="ArXiv paper", arxiv_id="2101.00001v2")
    _make(client, h, canonical_title="Neither paper")

    assert _titles(client.get("/api/v1/works?q=doi:10.1234/abc", headers=h)) == {"DOI paper"}
    assert _titles(client.get("/api/v1/works?q=arxiv:2101.00001", headers=h)) == {"ArXiv paper"}


def test_list_works_status_operator(client, auth_headers):
    h = auth_headers("editor")
    _make(client, h, canonical_title="Reading now", reading_status="reading")
    _make(client, h, canonical_title="Unread thing", reading_status="unread")
    assert _titles(client.get("/api/v1/works?q=status:reading", headers=h)) == {"Reading now"}


def test_list_works_has_abstract_summary_annotations(client, auth_headers, db):
    h = auth_headers("editor")
    with_abstract = _make(client, h, canonical_title="Has abstract", abstract="Some abstract")
    with_summary = _make(client, h, canonical_title="Has summary")
    with_notes = _make(client, h, canonical_title="Has notes")
    _make(client, h, canonical_title="Bare paper")

    db.add(
        Summary(
            entity_type="work",
            entity_id=uuid.UUID(with_summary["id"]),
            summary_type="extractive",
            text="s",
        )
    )
    db.add(
        Annotation(
            work_id=uuid.UUID(with_notes["id"]),
            annotation_type="note",
            content_markdown="a note",
        )
    )
    db.commit()

    assert with_abstract["canonical_title"] in _titles(
        client.get("/api/v1/works?q=has:abstract", headers=h)
    )
    assert _titles(client.get("/api/v1/works?q=has:summary", headers=h)) == {"Has summary"}
    assert _titles(client.get("/api/v1/works?q=has:notes", headers=h)) == {"Has notes"}
    assert _titles(client.get("/api/v1/works?q=has:annotations", headers=h)) == {"Has notes"}


def test_list_works_shelf_operator_respects_visibility(client, auth_headers, db, make_user):
    editor = auth_headers("editor")
    work = _make(client, editor, canonical_title="Secret shelf paper")
    # Put the work on a PRIVATE shelf.
    shelf = Shelf(name="Vault", access_level="private")
    db.add(shelf)
    db.commit()
    db.refresh(shelf)
    db.add(ShelfWork(shelf_id=shelf.id, work_id=uuid.UUID(work["id"])))
    db.commit()

    # A reader with no grant cannot even see the private shelf's work, so shelf:Vault yields
    # nothing (the operator never widens visibility).
    reader = make_user("reader-no-grant", role="reader")
    from app.services.auth import create_user_session

    token, _ = create_user_session(db, reader, ttl_minutes=60)
    db.commit()
    reader_h = {"Authorization": f"Bearer {token}"}
    assert _titles(client.get("/api/v1/works?q=shelf:Vault", headers=reader_h)) == set()

    # Grant the reader access to the shelf -> now shelf:Vault finds the work.
    group = Group(name="g-vault")
    db.add(group)
    db.commit()
    db.refresh(group)
    db.add(GroupMembership(group_id=group.id, user_id=reader.id))
    db.add(GroupGrant(group_id=group.id, target_type="shelf", target_id=shelf.id))
    db.commit()
    assert _titles(client.get("/api/v1/works?q=shelf:Vault", headers=reader_h)) == {
        "Secret shelf paper"
    }

    # The editor (owns the paper, sees the open-by-loose default? no — it is on a private shelf) —
    # as an editor without a grant they still cannot see it, confirming shelf: composes on SEE.
    assert _titles(client.get("/api/v1/works?q=shelf:Vault", headers=editor)) == set()


def test_list_works_rack_operator(client, auth_headers, db):
    h = auth_headers("owner")  # owner sees everything, isolating the rack join logic
    work = _make(client, h, canonical_title="Racked paper")
    shelf = Shelf(name="RackedShelf", access_level="open")
    rack = Rack(name="MyRack", access_level="open")
    db.add_all([shelf, rack])
    db.commit()
    db.refresh(shelf)
    db.refresh(rack)
    db.add(ShelfWork(shelf_id=shelf.id, work_id=uuid.UUID(work["id"])))
    db.add(RackShelf(rack_id=rack.id, shelf_id=shelf.id))
    db.commit()
    assert _titles(client.get("/api/v1/works?q=rack:MyRack", headers=h)) == {"Racked paper"}


def test_list_works_cites_and_cited_by_local(client, auth_headers, db, make_reference):
    h = auth_headers("owner")
    citing = _make(client, h, canonical_title="Citing survey")
    cited = _make(client, h, canonical_title="Foundational method")
    _make(client, h, canonical_title="Unconnected paper")

    # A resolved local citation edge: "Citing survey" cites "Foundational method".
    make_reference(
        db,
        citing_work_id=uuid.UUID(citing["id"]),
        resolved_work_id=uuid.UUID(cited["id"]),
        title="Foundational method",
        resolution_status="local_match",
    )
    db.commit()

    # cites:X -> works that cite the work matching X (the citing work).
    assert _titles(client.get("/api/v1/works?q=cites:Foundational", headers=h)) == {"Citing survey"}
    # cited_by_local:X -> works cited BY the work matching X (the cited target).
    assert _titles(client.get("/api/v1/works?q=cited_by_local:Citing", headers=h)) == {
        "Foundational method"
    }


def test_list_works_abstract_and_summary_operators(client, auth_headers, db):
    h = auth_headers("owner")
    abs_work = _make(
        client, h, canonical_title="Abstract carrier", abstract="uses diffusion models"
    )
    sum_work = _make(client, h, canonical_title="Summary carrier")
    _make(client, h, canonical_title="Bare thing")
    db.add(
        Summary(
            entity_type="work",
            entity_id=uuid.UUID(sum_work["id"]),
            summary_type="extractive",
            text="a study of reinforcement learning agents",
        )
    )
    db.commit()

    assert _titles(client.get("/api/v1/works?q=abstract:diffusion", headers=h)) == {
        abs_work["canonical_title"]
    }
    assert _titles(client.get("/api/v1/works?q=summary:reinforcement", headers=h)) == {
        sum_work["canonical_title"]
    }


def test_list_works_fulltext_and_file_operators(client, auth_headers, db):
    from app.models.chunk import WorkChunk
    from app.models.file import File, FileWorkLink

    h = auth_headers("owner")
    chunk_work = _make(client, h, canonical_title="Body text carrier")
    file_work = _make(client, h, canonical_title="File carrier")
    _make(client, h, canonical_title="Empty carrier")

    db.add(
        WorkChunk(
            work_id=uuid.UUID(chunk_work["id"]),
            section="Methods",
            position=0,
            text="we train a convolutional network end to end",
        )
    )
    file = File(sha256="a" * 64, size_bytes=10, original_filename="landmark-study.pdf")
    db.add(file)
    db.flush()
    db.add(FileWorkLink(file_id=file.id, work_id=uuid.UUID(file_work["id"])))
    db.commit()

    assert _titles(client.get("/api/v1/works?q=fulltext:convolutional", headers=h)) == {
        chunk_work["canonical_title"]
    }
    assert _titles(client.get("/api/v1/works?q=file:landmark", headers=h)) == {
        file_work["canonical_title"]
    }


def test_list_works_extraction_state_operators(client, auth_headers, db):
    from app.models.citation import RawTeiDocument
    from app.models.file import File, FileWorkLink

    h = auth_headers("owner")
    tei_work = _make(client, h, canonical_title="TEI carrier")
    ocr_work = _make(client, h, canonical_title="OCR carrier")
    _make(client, h, canonical_title="Plain carrier")

    tei_file = File(sha256="b" * 64, size_bytes=10, original_filename="tei.pdf")
    ocr_file = File(
        sha256="c" * 64, size_bytes=10, original_filename="scan.pdf", text_layer_quality="ocr_added"
    )
    db.add_all([tei_file, ocr_file])
    db.flush()
    db.add(RawTeiDocument(file_id=tei_file.id, work_id=uuid.UUID(tei_work["id"]), tei_xml="<TEI/>"))
    db.add(FileWorkLink(file_id=ocr_file.id, work_id=uuid.UUID(ocr_work["id"])))
    db.commit()

    assert _titles(client.get("/api/v1/works?q=has:grobid", headers=h)) == {
        tei_work["canonical_title"]
    }
    assert _titles(client.get("/api/v1/works?q=has:ocr", headers=h)) == {
        ocr_work["canonical_title"]
    }


def test_list_works_review_state_operators(client, auth_headers, db):
    from app.models.duplicate import DuplicateCandidate
    from app.models.file import File, FileWorkLink

    h = auth_headers("owner")
    dup_a = _make(client, h, canonical_title="Dup A")
    dup_b = _make(client, h, canonical_title="Dup B")
    versioned = _make(client, h, canonical_title="Versioned work")
    warned = _make(client, h, canonical_title="Warned work")
    _make(client, h, canonical_title="Clean work")

    db.add(
        DuplicateCandidate(
            candidate_type="fuzzy_title",
            entity_a_type="work",
            entity_a_id=uuid.UUID(dup_a["id"]),
            entity_b_type="work",
            entity_b_id=uuid.UUID(dup_b["id"]),
            score=0.9,
            status="open",
        )
    )
    # A shared version_group_id marks the work as part of a version group.
    from app.models.work import Work

    vw = db.get(Work, uuid.UUID(versioned["id"]))
    vw.version_group_id = vw.id
    warn_file = File(sha256="d" * 64, size_bytes=10, original_filename="w.pdf")
    db.add(warn_file)
    db.flush()
    db.add(
        FileWorkLink(
            file_id=warn_file.id,
            work_id=uuid.UUID(warned["id"]),
            warning_state="file_contains_multiple_works",
        )
    )
    db.commit()

    assert _titles(client.get("/api/v1/works?q=duplicate:yes", headers=h)) == {"Dup A", "Dup B"}
    assert _titles(client.get("/api/v1/works?q=version:yes", headers=h)) == {"Versioned work"}
    assert _titles(client.get("/api/v1/works?q=warning:*", headers=h)) == {"Warned work"}
    assert _titles(client.get("/api/v1/works?q=warning:multiple", headers=h)) == {"Warned work"}


def test_list_works_keyword_and_topic_operators(client, auth_headers, db):
    h = auth_headers("editor")
    kw = _make(client, h, canonical_title="Keyword carrier")
    tp = _make(client, h, canonical_title="Topic carrier")
    _make(client, h, canonical_title="Bare thing two")
    from app.models.work import Work

    db.get(Work, uuid.UUID(kw["id"])).keywords = ["graph embeddings", "retrieval"]
    db.get(Work, uuid.UUID(tp["id"])).topics = ["Neural Ranking"]
    db.commit()

    assert _titles(client.get("/api/v1/works?q=keyword:retrieval", headers=h)) == {
        "Keyword carrier"
    }
    # Case-insensitive; quoted phrases work like the other operators.
    assert _titles(client.get('/api/v1/works?q=topic:"neural ranking"', headers=h)) == {
        "Topic carrier"
    }
    assert _titles(client.get("/api/v1/works?q=keyword:nomatchxyz", headers=h)) == set()
