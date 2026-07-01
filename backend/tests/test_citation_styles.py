"""Real CSL citation-style rendering (Phase B4).

Exercises the citeproc-py-backed ``styled`` export: distinct output per style (author-date vs
numbered), the newly added styles (mla/harvard/vancouver/nature), graceful handling of
missing-field items, the styles-list endpoint, and the end-to-end API path.
"""

import pytest
from app.models.metadata import MetadataAssertion
from app.models.organization import Shelf, ShelfWork
from app.models.work import Work
from app.services import csl
from app.services.csl import engine
from app.services.export_service import _Entry, render_styled


class _Work:
    """Lightweight stand-in for a ``Work`` row for pure-render unit tests (no DB needed)."""

    def __init__(self, **kw):
        self.canonical_title = kw.get("title")
        self.year = kw.get("year")
        self.venue = kw.get("venue")
        self.doi = kw.get("doi")
        self.abstract = kw.get("abstract")
        self.work_type = kw.get("work_type", "article")


def _entry():
    return _Entry(
        work=_Work(
            title="Attention Is All You Need",
            year=2017,
            venue="NeurIPS",
            doi="10.5555/3295222.3295349",
        ),
        authors=["Ashish Vaswani", "Noam Shazeer"],
        key="vaswani2017",
        meta={"volume": "30", "issue": "1", "pages": "5998-6008"},
    )


# --- style list -------------------------------------------------------------


def test_styles_list_includes_old_and_new_styles():
    keys = {s["value"] for s in csl.available_styles()}
    # The pre-B4 keys still work...
    assert {"apa", "ieee", "chicago"} <= keys
    # ...and the new common ones are offered.
    assert {"mla", "harvard", "vancouver", "nature"} <= keys
    assert set(csl.CITATION_STYLES) == keys
    # Every style carries a human label.
    assert all(s["label"] for s in csl.available_styles())


# --- distinct rendering per style -------------------------------------------


def test_apa_is_author_date():
    out = render_styled([_entry()], "apa")
    assert "Vaswani" in out and "(2017)" in out
    assert "Attention Is All You Need" in out
    # APA is author-date, NOT a numbered list.
    assert not out.startswith("[1]") and not out.startswith("1.")


def test_ieee_is_numbered_and_distinct_from_apa():
    ieee = render_styled([_entry()], "ieee")
    apa = render_styled([_entry()], "apa")
    # IEEE is a bracketed numbered style.
    assert ieee.startswith("[1]")
    assert ieee != apa  # style-distinct output


def test_mla_renders_and_differs_from_apa_and_ieee():
    mla = render_styled([_entry()], "mla")
    apa = render_styled([_entry()], "apa")
    ieee = render_styled([_entry()], "ieee")
    assert "Attention Is All You Need" in mla
    assert mla != apa and mla != ieee
    assert not mla.startswith("[1]")  # MLA is not IEEE-style numbered


def test_vancouver_is_numbered():
    out = render_styled([_entry()], "vancouver")
    assert out.startswith("1.")


def test_all_styles_produce_nonempty_distinct_output():
    rendered = {s: render_styled([_entry()], s) for s in csl.CITATION_STYLES}
    assert all(text.strip() for text in rendered.values())
    # At least author-date (apa) and numbered (ieee) are mutually distinct.
    assert len(set(rendered.values())) >= 2


# --- graceful failure / missing fields --------------------------------------


def test_missing_fields_do_not_crash():
    bare = _Entry(work=_Work(title=None, work_type=""), authors=[], key="worknd", meta={})
    for style in csl.CITATION_STYLES:
        out = render_styled([bare], style)  # must not raise
        assert isinstance(out, str)


def test_empty_entry_list_returns_empty_string():
    assert render_styled([], "apa") == ""


def test_unknown_style_raises_value_error():
    with pytest.raises(ValueError, match="Unsupported citation style"):
        render_styled([_entry()], "bogus-style")


def test_render_failure_falls_back_to_safe_string(monkeypatch):
    """If citeproc blows up for the whole list, we get a minimal per-item fallback, not a crash."""

    class _Boom:
        def register(self, *a, **k):
            pass

        def bibliography(self):
            raise RuntimeError("citeproc exploded")

    monkeypatch.setattr(engine, "_load_style", lambda style: object())
    monkeypatch.setattr(
        "citeproc.CitationStylesBibliography", lambda *a, **k: _Boom(), raising=False
    )
    out = render_styled([_entry()], "apa")
    assert "Attention Is All You Need" in out  # safe fallback still names the work


# --- CSL-JSON item mapping --------------------------------------------------


def test_work_maps_to_csl_json_item_fields():
    from app.services.export_service import _entry_to_csl_item

    item = _entry_to_csl_item(_entry())
    assert item["type"] == "article-journal"
    assert item["title"] == "Attention Is All You Need"
    assert item["issued"] == {"date-parts": [[2017]]}
    assert item["container-title"] == "NeurIPS"
    assert item["DOI"] == "10.5555/3295222.3295349"
    assert item["volume"] == "30" and item["issue"] == "1" and item["page"] == "5998-6008"
    assert {"family": "Vaswani", "given": "Ashish"} in item["author"]


def test_work_type_maps_to_csl_type():
    from app.services.export_service import _entry_to_csl_item

    conf = _Entry(work=_Work(title="X", work_type="inproceedings"), key="x")
    assert _entry_to_csl_item(conf)["type"] == "paper-conference"
    book = _Entry(work=_Work(title="Y", work_type="book"), key="y")
    assert _entry_to_csl_item(book)["type"] == "book"


# --- API end-to-end ---------------------------------------------------------


def _seed_shelf(db):
    work = Work(
        canonical_title="Attention Is All You Need",
        normalized_title="attention is all you need",
        year=2017,
        doi="10.5555/3295222.3295349",
        venue="NeurIPS",
        work_type="article",
    )
    shelf = Shelf(name="Transformers")
    db.add_all([work, shelf])
    db.flush()
    db.add(ShelfWork(shelf_id=shelf.id, work_id=work.id))
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


def test_styled_export_endpoint(client, auth_headers, db):
    shelf = _seed_shelf(db)
    r = client.post(
        "/api/v1/exports",
        headers=auth_headers("reader"),
        json={
            "target_type": "shelf",
            "target_id": str(shelf.id),
            "format": "styled",
            "style": "ieee",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["content_type"] == "text/plain"
    assert body["content"].startswith("[1]")
    assert "Vaswani" in body["content"]


def test_styles_endpoint(client, auth_headers):
    r = client.get("/api/v1/exports/styles", headers=auth_headers("reader"))
    assert r.status_code == 200
    values = {s["value"] for s in r.json()}
    assert {"apa", "ieee", "chicago", "mla", "harvard", "vancouver", "nature"} <= values
