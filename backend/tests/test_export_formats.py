"""Export format coverage: BibTeX/BibLaTeX/RIS/CSL-JSON/Markdown/HTML + audit.

Companion to the enabled `test_export_shelf_as_bibtex` acceptance test; this exercises the
remaining formats added in the M3 export expansion, author inclusion (from metadata
assertions), per-format media types, and the `paper.exported` audit event (SPEC §7.6/§8.13).
"""

import json

from app.models.audit import AuditEvent
from app.models.metadata import MetadataAssertion
from app.models.organization import Shelf, ShelfWork
from app.models.work import Work


def _seed_shelf(db, *, with_authors=True):
    """A shelf holding one work (optionally with an authors assertion). Returns the shelf."""
    work = Work(
        canonical_title="Attention Is All You Need",
        normalized_title="attention is all you need",
        year=2017,
        doi="10.5555/3295222.3295349",
        venue="NeurIPS",
        abstract="The dominant sequence transduction models...",
    )
    shelf = Shelf(name="Transformers")
    db.add_all([work, shelf])
    db.flush()
    db.add(ShelfWork(shelf_id=shelf.id, work_id=work.id))
    if with_authors:
        db.add(
            MetadataAssertion(
                entity_type="work",
                entity_id=work.id,
                field_name="authors",
                value="Ashish Vaswani; Noam Shazeer",
                source="arxiv",
                confidence=0.9,
                selected_as_canonical=True,
            )
        )
    db.commit()
    return shelf


def _export(client, headers, shelf, fmt):
    return client.post(
        "/api/v1/exports",
        headers=headers,
        json={"target_type": "shelf", "target_id": str(shelf.id), "format": fmt},
    )


def test_bibtex_includes_authors(client, auth_headers, db):
    shelf = _seed_shelf(db)
    body = _export(client, auth_headers("reader"), shelf, "bibtex").json()
    assert body["filename"].endswith(".bib")
    assert body["content_type"] == "application/x-bibtex"
    assert "author = {Ashish Vaswani and Noam Shazeer}" in body["content"]
    assert "@article{vaswani2017" in body["content"]


def test_biblatex_uses_date_and_journaltitle(client, auth_headers, db):
    shelf = _seed_shelf(db)
    content = _export(client, auth_headers("editor"), shelf, "biblatex").json()["content"]
    assert "date = {2017}" in content
    assert "journaltitle = {NeurIPS}" in content


def test_ris_format(client, auth_headers, db):
    shelf = _seed_shelf(db)
    body = _export(client, auth_headers("reader"), shelf, "ris").json()
    assert body["filename"].endswith(".ris")
    content = body["content"]
    assert content.startswith("TY  - JOUR")
    assert "AU  - Ashish Vaswani" in content
    assert "TI  - Attention Is All You Need" in content
    assert "PY  - 2017" in content
    assert content.rstrip().endswith("ER  -")


def test_csl_json_is_valid_and_structured(client, auth_headers, db):
    shelf = _seed_shelf(db)
    body = _export(client, auth_headers("reader"), shelf, "csl-json").json()
    assert body["content_type"] == "application/vnd.citationstyles.csl+json"
    items = json.loads(body["content"])
    assert len(items) == 1
    item = items[0]
    assert item["type"] == "article-journal"
    assert item["title"] == "Attention Is All You Need"
    assert item["issued"] == {"date-parts": [[2017]]}
    assert item["DOI"] == "10.5555/3295222.3295349"
    assert {"family": "Vaswani", "given": "Ashish"} in item["author"]


def test_markdown_format(client, auth_headers, db):
    shelf = _seed_shelf(db)
    content = _export(client, auth_headers("reader"), shelf, "markdown").json()["content"]
    assert content.startswith("# Bibliography")
    assert "**Attention Is All You Need** (2017)." in content
    assert "DOI: [10.5555/3295222.3295349](https://doi.org/10.5555/3295222.3295349)" in content


def test_html_format_escapes(client, auth_headers, db):
    work = Work(canonical_title="A < B & C", normalized_title="a < b & c", year=2021)
    shelf = Shelf(name="Edge")
    db.add_all([work, shelf])
    db.flush()
    db.add(ShelfWork(shelf_id=shelf.id, work_id=work.id))
    db.commit()
    body = _export(client, auth_headers("reader"), shelf, "html").json()
    assert body["content_type"] == "text/html"
    content = body["content"]
    assert "<ol>" in content and "<li>" in content
    assert "A &lt; B &amp; C" in content  # escaped, not raw


def test_unsupported_format_is_rejected(client, auth_headers, db):
    shelf = _seed_shelf(db)
    r = _export(client, auth_headers("reader"), shelf, "docx")
    assert r.status_code == 400


def test_export_records_paper_exported_audit(client, auth_headers, db):
    shelf = _seed_shelf(db)
    assert _export(client, auth_headers("editor"), shelf, "ris").status_code == 200
    events = db.query(AuditEvent).filter(AuditEvent.event_type == "paper.exported").all()
    assert len(events) == 1
    assert events[0].details["format"] == "ris"
    assert events[0].details["work_count"] == 1
    assert events[0].entity_type == "shelf"
