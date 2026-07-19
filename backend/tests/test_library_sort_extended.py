"""list_works extended sort: DOI, shelves/racks/rows (SEE-filtered min-name aggregates), and
multi-column sort priority. Extends the #3 sort allowlist suite."""

from app.models.organization import (
    Rack,
    RackShelf,
    Row,
    RowRack,
    Shelf,
    ShelfWork,
)
from app.models.work import Work


def _work(db, title: str, **kwargs) -> Work:
    work = Work(canonical_title=title, normalized_title=title.lower(), **kwargs)
    db.add(work)
    db.flush()
    return work


def _ids(client, headers, **params) -> list[str]:
    resp = client.get("/api/v1/works", headers=headers, params=params)
    assert resp.status_code == 200, resp.text
    return [w["id"] for w in resp.json()["items"]]


def test_sort_by_doi_lexical_with_nulls_last(client, auth_headers, db):
    z = _work(db, "Zed", doi="10.1000/zzz")
    a = _work(db, "Ada", doi="10.1000/aaa")
    none = _work(db, "NoDoi")  # no DOI → ordered last regardless of direction
    db.commit()
    h = auth_headers("owner")

    asc = _ids(client, h, sort="doi", order="asc")
    assert asc.index(str(a.id)) < asc.index(str(z.id))
    assert asc[-1] == str(none.id)  # NULL DOI last

    desc = _ids(client, h, sort="doi", order="desc")
    assert desc.index(str(z.id)) < desc.index(str(a.id))
    assert desc[-1] == str(none.id)  # NULL DOI still last (nullslast)


def test_sort_by_shelves_min_name(client, auth_headers, db):
    zeta = Shelf(name="Zeta shelf")
    alpha = Shelf(name="Alpha shelf")
    db.add_all([zeta, alpha])
    db.flush()
    on_zeta = _work(db, "On Zeta")
    on_alpha = _work(db, "On Alpha")
    shelfless = _work(db, "Shelfless")
    db.add_all(
        [
            ShelfWork(shelf_id=zeta.id, work_id=on_zeta.id),
            ShelfWork(shelf_id=alpha.id, work_id=on_alpha.id),
        ]
    )
    db.commit()
    h = auth_headers("owner")

    asc = _ids(client, h, sort="shelves", order="asc")
    assert asc.index(str(on_alpha.id)) < asc.index(str(on_zeta.id))  # Alpha < Zeta
    assert asc[-1] == str(shelfless.id)  # no visible shelf → last


def test_sort_by_shelves_uses_alphabetically_first_of_many(client, auth_headers, db):
    """A work on several shelves sorts by its alphabetically-first (min) shelf name."""
    mid = Shelf(name="Mango")
    early = Shelf(name="Banana")
    late = Shelf(name="Yak")
    db.add_all([mid, early, late])
    db.flush()
    multi = _work(db, "Multi shelf")  # on Mango + Banana + Yak → sorts as "Banana"
    other = _work(db, "Other")  # on Cherry
    cherry = Shelf(name="Cherry")
    db.add(cherry)
    db.flush()
    db.add_all(
        [
            ShelfWork(shelf_id=mid.id, work_id=multi.id),
            ShelfWork(shelf_id=early.id, work_id=multi.id),
            ShelfWork(shelf_id=late.id, work_id=multi.id),
            ShelfWork(shelf_id=cherry.id, work_id=other.id),
        ]
    )
    db.commit()
    h = auth_headers("owner")

    asc = _ids(client, h, sort="shelves", order="asc")
    # multi's min shelf "Banana" < other's "Cherry".
    assert asc.index(str(multi.id)) < asc.index(str(other.id))


def test_sort_by_racks_and_rows(client, auth_headers, db):
    # work→shelf→rack→row chain, two independent branches with A-before-Z names.
    shelf_a = Shelf(name="s-a")
    shelf_z = Shelf(name="s-z")
    rack_a = Rack(name="Rack Alpha")
    rack_z = Rack(name="Rack Zeta")
    row_a = Row(name="Row Alpha")
    row_z = Row(name="Row Zeta")
    db.add_all([shelf_a, shelf_z, rack_a, rack_z, row_a, row_z])
    db.flush()
    w_a = _work(db, "In Alpha branch")
    w_z = _work(db, "In Zeta branch")
    loose = _work(db, "Loose")  # in no container
    db.add_all(
        [
            ShelfWork(shelf_id=shelf_a.id, work_id=w_a.id),
            ShelfWork(shelf_id=shelf_z.id, work_id=w_z.id),
            RackShelf(rack_id=rack_a.id, shelf_id=shelf_a.id),
            RackShelf(rack_id=rack_z.id, shelf_id=shelf_z.id),
            RowRack(row_id=row_a.id, rack_id=rack_a.id),
            RowRack(row_id=row_z.id, rack_id=rack_z.id),
        ]
    )
    db.commit()
    h = auth_headers("owner")

    racks_asc = _ids(client, h, sort="racks", order="asc")
    assert racks_asc.index(str(w_a.id)) < racks_asc.index(str(w_z.id))
    assert racks_asc[-1] == str(loose.id)

    rows_asc = _ids(client, h, sort="rows", order="asc")
    assert rows_asc.index(str(w_a.id)) < rows_asc.index(str(w_z.id))
    assert rows_asc[-1] == str(loose.id)


def test_multi_column_sort_priority(client, auth_headers, db):
    """`sort=year:desc,title:asc` groups by year (newest first), then A→Z within a year."""
    y2020_b = _work(db, "Bravo", year=2020)
    y2020_a = _work(db, "Alpha", year=2020)
    y2019_c = _work(db, "Charlie", year=2019)
    db.commit()
    h = auth_headers("owner")

    ids = _ids(client, h, sort="year:desc,title:asc")
    # 2020 group first (A before B), then 2019.
    assert ids == [str(y2020_a.id), str(y2020_b.id), str(y2019_c.id)]


def test_multi_column_sort_shared_order_fallback(client, auth_headers, db):
    """Entries without their own direction inherit the shared `order` param; unknown keys are
    skipped, leaving the valid ones."""
    y2020_b = _work(db, "Bravo", year=2020)
    y2020_a = _work(db, "Alpha", year=2020)
    y2019_z = _work(db, "Zeta", year=2019)
    db.commit()
    h = auth_headers("owner")

    # "year" (no dir) + "bogus" (skipped) + "title" (no dir), shared order=asc.
    ids = _ids(client, h, sort="year,bogus,title", order="asc")
    # year asc → 2019 first, then 2020 group ordered title asc (Alpha before Bravo).
    assert ids == [str(y2019_z.id), str(y2020_a.id), str(y2020_b.id)]
