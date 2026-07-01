"""GET /imports/batches access-filtered listing (Phase B6, import-batch graph scope picker)."""

from app.models.source import ImportBatch
from app.models.work import Work
from app.services.auth import create_user_session


def _seed_batch(db, *, created_by, input_type="bibtex") -> ImportBatch:
    batch = ImportBatch(created_by_user_id=created_by, input_type=input_type, status="completed")
    db.add(batch)
    db.flush()
    db.add(
        Work(
            canonical_title="W",
            normalized_title="w",
            import_batch_id=batch.id,
        )
    )
    db.commit()
    db.refresh(batch)
    return batch


def _headers_for(db, user) -> dict[str, str]:
    token, _ = create_user_session(db, user, ttl_minutes=60)
    db.commit()
    return {"Authorization": f"Bearer {token}"}


def test_reader_sees_only_own_batches(client, db, make_user) -> None:
    alice = make_user("alice", role="reader")
    bob = make_user("bob", role="reader")
    mine = _seed_batch(db, created_by=alice.id)
    _seed_batch(db, created_by=bob.id)

    resp = client.get("/api/v1/imports/batches", headers=_headers_for(db, alice))
    assert resp.status_code == 200
    body = resp.json()
    assert [b["id"] for b in body] == [str(mine.id)]
    assert body[0]["work_count"] == 1
    assert body[0]["created_by_user_id"] == str(alice.id)


def test_owner_sees_all_batches_newest_first(client, db, make_user) -> None:
    alice = make_user("alice2", role="reader")
    owner = make_user("owner2", role="owner")
    first = _seed_batch(db, created_by=alice.id)
    second = _seed_batch(db, created_by=owner.id)

    resp = client.get("/api/v1/imports/batches", headers=_headers_for(db, owner))
    assert resp.status_code == 200
    ids = [b["id"] for b in resp.json()]
    assert set(ids) == {str(first.id), str(second.id)}
    # Newest first (second was created after first).
    assert ids.index(str(second.id)) < ids.index(str(first.id))
