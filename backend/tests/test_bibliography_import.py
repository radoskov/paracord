"""RIS and CSL-JSON import tests (Stage 4 / 6D)."""

from app.models.work import Work
from app.services.bibliography_import import parse_csl, parse_ris

_RIS = """TY  - JOUR
TI  - Attention Is All You Need
AU  - Vaswani, Ashish
AU  - Shazeer, Noam
PY  - 2017
DO  - 10.5555/ATTN
JO  - NeurIPS
AB  - We propose the Transformer.
ER  -
"""

_CSL = """[
  {
    "title": "Deep Residual Learning for Image Recognition",
    "DOI": "10.1109/CVPR.2016.90",
    "issued": {"date-parts": [[2016]]},
    "author": [{"given": "Kaiming", "family": "He"}],
    "container-title": "CVPR",
    "type": "paper-conference",
    "abstract": "We present a residual learning framework."
  }
]"""


# --- parser unit tests ------------------------------------------------------


def test_parse_ris_extracts_fields() -> None:
    records = parse_ris(_RIS)
    assert len(records) == 1
    record = records[0]
    assert record.title == "Attention Is All You Need"
    assert record.doi == "10.5555/ATTN"
    assert record.year == 2017
    assert record.venue == "NeurIPS"
    assert record.authors == ["Vaswani, Ashish", "Shazeer, Noam"]


def test_parse_csl_extracts_fields() -> None:
    records = parse_csl(_CSL)
    assert len(records) == 1
    record = records[0]
    assert record.title.startswith("Deep Residual")
    assert record.doi == "10.1109/CVPR.2016.90"
    assert record.year == 2016
    assert record.venue == "CVPR"
    assert record.authors == ["Kaiming He"]


# --- API tests --------------------------------------------------------------


def test_ris_import_creates_and_dedups(client, auth_headers, db) -> None:
    headers = auth_headers("editor")
    first = client.post("/api/v1/imports/ris", headers=headers, json={"content": _RIS})
    assert first.status_code == 201
    assert first.json()["stats"]["created"] == 1
    # DOI is normalised on write, so a re-import is matched, not duplicated.
    work = db.query(Work).filter(Work.normalized_title == "attention is all you need").first()
    assert work is not None
    assert work.doi == "10.5555/attn"

    second = client.post("/api/v1/imports/ris", headers=headers, json={"content": _RIS})
    assert second.status_code == 201
    assert second.json()["stats"]["matched"] == 1
    assert second.json()["stats"]["created"] == 0


def test_csl_import_creates_work(client, auth_headers, db) -> None:
    headers = auth_headers("editor")
    response = client.post("/api/v1/imports/csl", headers=headers, json={"content": _CSL})
    assert response.status_code == 201
    assert response.json()["stats"]["created"] == 1
    work = db.query(Work).filter(Work.doi == "10.1109/cvpr.2016.90").first()
    assert work is not None
    assert work.year == 2016


def test_csl_import_rejects_invalid_json(client, auth_headers) -> None:
    response = client.post(
        "/api/v1/imports/csl", headers=auth_headers("editor"), json={"content": "{not json"}
    )
    assert response.status_code == 400


def test_ris_import_requires_auth(client) -> None:
    assert client.post("/api/v1/imports/ris", json={"content": _RIS}).status_code == 401


def test_bibliography_import_requires_editor(client, auth_headers) -> None:
    response = client.post(
        "/api/v1/imports/ris", headers=auth_headers("reader"), json={"content": _RIS}
    )
    assert response.status_code == 403
