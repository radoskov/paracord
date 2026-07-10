"""Batch 10 (issue 5): the Library list endpoint enriches each row with file_count, tags, and
status badges (extraction / text-layer / conflict), batched across the page.
"""

from app.models.file import File, FileWorkLink
from app.models.metadata import MetadataAssertion
from app.models.organization import Tag, TagLink
from app.models.work import Work


def _work(db, title: str, **kwargs) -> Work:
    work = Work(canonical_title=title, normalized_title=title.lower(), **kwargs)
    db.add(work)
    db.flush()
    return work


def _file(db, sha: str, *, status: str = "extracted", quality: str = "good") -> File:
    file = File(
        sha256=sha, size_bytes=100, original_filename="p.pdf", status=status, text_layer_quality=quality
    )
    db.add(file)
    db.flush()
    return file


def _items_by_id(payload: dict) -> dict[str, dict]:
    return {item["id"]: item for item in payload["items"]}


def test_library_columns_file_count_tags_and_badges(client, auth_headers, db):
    # Work A: one failed extraction with a poor text layer.
    a = _work(db, "Alpha")
    fa = _file(db, "a" * 64, status="extract_failed", quality="poor")
    db.add(FileWorkLink(file_id=fa.id, work_id=a.id))

    # Work B: two extracted files, a tag, and a title conflict (two distinct assertions).
    b = _work(db, "Beta")
    db.add(FileWorkLink(file_id=_file(db, "b" * 64).id, work_id=b.id))
    db.add(FileWorkLink(file_id=_file(db, "c" * 64).id, work_id=b.id))
    tag = Tag(name="ml", normalized_name="ml", color="#ff0000")
    db.add(tag)
    db.flush()
    db.add(TagLink(tag_id=tag.id, entity_type="work", entity_id=b.id))
    db.add_all(
        [
            MetadataAssertion(entity_type="work", entity_id=b.id, field_name="title", value="Beta", source="grobid"),
            MetadataAssertion(entity_type="work", entity_id=b.id, field_name="title", value="Beta v2", source="crossref"),
        ]
    )

    # Work C: a not-yet-extracted local-agent stub (no files).
    c = _work(db, "Gamma", canonical_metadata_source="agent_index_only")
    db.commit()

    resp = client.get("/api/v1/works", headers=auth_headers("owner"))
    assert resp.status_code == 200
    items = _items_by_id(resp.json())

    assert items[str(a.id)]["file_count"] == 1
    assert set(items[str(a.id)]["badges"]) == {"extract_failed", "text_poor"}
    assert items[str(a.id)]["tags"] == []

    assert items[str(b.id)]["file_count"] == 2
    assert "extracted" in items[str(b.id)]["badges"]
    assert "conflicts" in items[str(b.id)]["badges"]
    assert [t["name"] for t in items[str(b.id)]["tags"]] == ["ml"]
    assert items[str(b.id)]["tags"][0]["color"] == "#ff0000"

    assert items[str(c.id)]["file_count"] == 0
    assert items[str(c.id)]["badges"] == ["not_extracted"]
