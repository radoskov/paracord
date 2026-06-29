"""Additional current-stage library-contract tests.

The assertions here mirror the product specification without over-constraining
implementation details: papers may be on multiple shelves, shelves may be in
multiple racks, membership operations are idempotent, and shelf/rack tags do not
accidentally contaminate work-tag filters.
"""

from __future__ import annotations


def _create_work(client, headers, title: str) -> dict:
    response = client.post(
        "/api/v1/works",
        headers=headers,
        json={"canonical_title": title, "reading_status": "unread"},
    )
    assert response.status_code == 201
    return response.json()


def _create_shelf(client, headers, name: str) -> dict:
    response = client.post("/api/v1/shelves", headers=headers, json={"name": name})
    assert response.status_code == 201
    return response.json()


def _create_rack(client, headers, name: str) -> dict:
    response = client.post("/api/v1/racks", headers=headers, json={"name": name})
    assert response.status_code == 201
    return response.json()


def test_work_can_appear_in_multiple_shelves_and_rack_filters_remain_distinct(
    client,
    auth_headers,
) -> None:
    headers = auth_headers("editor")
    work = _create_work(client, headers, "Shared Work")
    shelf_a = _create_shelf(client, headers, "Shelf A")
    shelf_b = _create_shelf(client, headers, "Shelf B")
    rack = _create_rack(client, headers, "Rack")

    for shelf in (shelf_a, shelf_b):
        assert (
            client.post(
                f"/api/v1/shelves/{shelf['id']}/works",
                headers=headers,
                json={"work_id": work["id"]},
            ).status_code
            == 204
        )
        assert (
            client.post(
                f"/api/v1/racks/{rack['id']}/shelves",
                headers=headers,
                json={"shelf_id": shelf["id"]},
            ).status_code
            == 204
        )

    for shelf in (shelf_a, shelf_b):
        shelf_works = client.get(
            f"/api/v1/shelves/{shelf['id']}/works",
            headers=headers,
        ).json()
        assert [item["id"] for item in shelf_works] == [work["id"]]

    rack_filtered_works = client.get(
        f"/api/v1/works?rack_id={rack['id']}",
        headers=headers,
    ).json()
    assert [item["id"] for item in rack_filtered_works] == [work["id"]]


def test_shelf_and_rack_membership_writes_are_idempotent(client, auth_headers) -> None:
    headers = auth_headers("editor")
    work = _create_work(client, headers, "Idempotent Work")
    shelf = _create_shelf(client, headers, "Idempotent Shelf")
    rack = _create_rack(client, headers, "Idempotent Rack")

    for position in (10, 20):
        assert (
            client.post(
                f"/api/v1/shelves/{shelf['id']}/works",
                headers=headers,
                json={"work_id": work["id"], "position": position},
            ).status_code
            == 204
        )
        assert (
            client.post(
                f"/api/v1/racks/{rack['id']}/shelves",
                headers=headers,
                json={"shelf_id": shelf["id"], "position": position},
            ).status_code
            == 204
        )

    assert len(client.get(f"/api/v1/shelves/{shelf['id']}/works", headers=headers).json()) == 1
    assert len(client.get(f"/api/v1/racks/{rack['id']}/shelves", headers=headers).json()) == 1


def test_tags_on_shelves_and_racks_do_not_match_work_tag_filters(client, auth_headers) -> None:
    headers = auth_headers("editor")
    work = _create_work(client, headers, "Tagged Work")
    shelf = _create_shelf(client, headers, "Tagged Shelf")
    rack = _create_rack(client, headers, "Tagged Rack")

    tag = client.post(
        "/api/v1/tags",
        headers=headers,
        json={"name": "review-cluster"},
    ).json()

    for entity_type, entity_id in (("shelf", shelf["id"]), ("rack", rack["id"])):
        assert (
            client.post(
                f"/api/v1/tags/{tag['id']}/links",
                headers=headers,
                json={"entity_type": entity_type, "entity_id": entity_id},
            ).status_code
            == 204
        )

    assert client.get(f"/api/v1/works?tag_id={tag['id']}", headers=headers).json() == []

    assert (
        client.post(
            f"/api/v1/tags/{tag['id']}/links",
            headers=headers,
            json={"entity_type": "work", "entity_id": work["id"]},
        ).status_code
        == 204
    )

    tagged_works = client.get(f"/api/v1/works?tag_id={tag['id']}", headers=headers).json()
    assert [item["id"] for item in tagged_works] == [work["id"]]
