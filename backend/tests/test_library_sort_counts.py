"""Count-column sorts on the library list (file_count etc.) actually order the page (2026-07-14).

Reported: clicking the file-count header flipped the sort arrow but the order never changed.
These pin the server-side ORDER BY for the correlated-subquery sort columns.
"""

from app.models.file import File, FileWorkLink
from app.models.work import Work


def _work(db, title: str) -> Work:
    work = Work(canonical_title=title, normalized_title=title.lower())
    db.add(work)
    db.flush()
    return work


def _attach_files(db, work: Work, n: int, prefix: str) -> None:
    for i in range(n):
        file = File(
            sha256=(prefix * 64)[:63] + str(i),
            size_bytes=100,
            original_filename="p.pdf",
            status="extracted",
        )
        db.add(file)
        db.flush()
        db.add(FileWorkLink(file_id=file.id, work_id=work.id))


def test_sort_by_file_count_orders_both_directions(client, auth_headers, db):
    headers = auth_headers("editor")
    a = _work(db, "Alpha")
    b = _work(db, "Beta")
    c = _work(db, "Gamma")
    _attach_files(db, a, 1, "a")
    _attach_files(db, c, 3, "c")
    db.commit()

    resp = client.get("/api/v1/works?sort=file_count&order=desc", headers=headers)
    assert resp.status_code == 200
    counts = [item["file_count"] for item in resp.json()["items"]]
    assert counts == sorted(counts, reverse=True), counts
    assert resp.json()["items"][0]["id"] == str(c.id)

    resp = client.get("/api/v1/works?sort=file_count&order=asc", headers=headers)
    counts = [item["file_count"] for item in resp.json()["items"]]
    assert counts == sorted(counts), counts
    assert resp.json()["items"][0]["id"] == str(b.id)
