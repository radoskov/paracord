"""Reading-queue manual ordering (SPEC §8.17.1)."""


def _reading_work(client, h, title):
    wid = client.post("/api/v1/works", headers=h, json={"canonical_title": title}).json()["id"]
    client.patch(f"/api/v1/works/{wid}", headers=h, json={"reading_status": "reading"})
    return wid


def test_reading_queue_lists_only_reading_and_reorders(client, auth_headers):
    h = auth_headers("editor")
    a = _reading_work(client, h, "Queue A")
    b = _reading_work(client, h, "Queue B")
    c = _reading_work(client, h, "Queue C")
    # An unread paper must not appear in the queue.
    client.post("/api/v1/works", headers=h, json={"canonical_title": "Not reading"})

    queue = client.get("/api/v1/works/reading-queue", headers=h).json()
    assert {w["canonical_title"] for w in queue} == {"Queue A", "Queue B", "Queue C"}

    reordered = client.post(
        "/api/v1/works/reading-queue/reorder", headers=h, json={"work_ids": [c, a, b]}
    ).json()
    assert [w["id"] for w in reordered] == [c, a, b]
    # Order persists on a fresh read.
    again = client.get("/api/v1/works/reading-queue", headers=h).json()
    assert [w["id"] for w in again] == [c, a, b]
