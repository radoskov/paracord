"""Agent operations tests (§32 S4) with a fake server client."""

from __future__ import annotations

import asyncio
from pathlib import Path

from paperracks_agent import agent_ops
from paperracks_agent.config import AgentConfig, ManagedFile, ManagedFolder
from paperracks_agent.state import AgentState


class FakeClient:
    """Records calls instead of hitting a server."""

    def __init__(
        self, pending=None, my_files=None, extraction_queued=True, max_batch_items=100
    ) -> None:
        self.server_url = "http://test"
        self._max_batch_items = max_batch_items
        self.manifests: list[dict] = []
        self.extracted: list[str] = []
        self.teleported: list[str] = []
        self.rejected: list[str] = []
        self.removed: list[str] = []
        self.offered: list[str] = []
        self._pending = pending or []
        self._my_files = my_files or []
        # Simulate the server's D7 report of whether it could queue extraction.
        self._extraction_queued = extraction_queued

    async def send_manifest(self, payload: dict) -> None:
        self.manifests.append(payload)

    async def get_me(self) -> dict:
        return {"status": "approved", "max_batch_items": self._max_batch_items}

    async def get_my_files(self) -> list[dict]:
        return self._my_files

    async def report_source_removed(self, ids: list[str]) -> dict:
        self.removed.extend(ids)
        return {"marked": len(ids)}

    async def get_pending_teleports(self) -> list[dict]:
        return self._pending

    async def upload_for_extraction(self, local_file_id: str, handle) -> dict:
        self.extracted.append(local_file_id)
        return {"status": "extracting", "extraction_queued": self._extraction_queued}

    async def upload_teleport_content(self, local_file_id: str, handle) -> dict:
        self.teleported.append(local_file_id)
        return {"status": "complete"}

    async def offer_teleport(self, local_file_id: str, handle) -> dict:
        self.offered.append(local_file_id)
        return {"status": "complete"}

    async def reject_teleport(self, local_file_id: str, forever: bool = False) -> dict:
        self.rejected.append(local_file_id)
        return {"status": "rejected"}


def _folder(tmp_path: Path, action: str, policy: str = "ask") -> AgentConfig:
    root = tmp_path / "papers"
    root.mkdir()
    (root / "a.pdf").write_bytes(b"%PDF-1.4\nA")
    return AgentConfig(folders=[ManagedFolder(path=root, action=action, teleport_policy=policy)])


def test_sync_chunks_oversized_manifest(tmp_path: Path) -> None:
    """A scan larger than the server's max_batch_items is split into sequential ≤cap manifests."""
    root = tmp_path / "papers"
    root.mkdir()
    for i in range(5):
        (root / f"paper-{i}.pdf").write_bytes(f"%PDF-1.4\n{i}".encode())
    config = AgentConfig(folders=[ManagedFolder(path=root, action="index_only")])
    state = AgentState(tmp_path / "state.sqlite3")
    client = FakeClient(max_batch_items=2)

    summary = asyncio.run(agent_ops.sync(config, state, client))

    assert summary["indexed"] == 5
    # 5 items at a cap of 2 -> chunks of 2, 2, 1.
    assert [len(m["items"]) for m in client.manifests] == [2, 2, 1]
    sent = {item["local_file_id"] for m in client.manifests for item in m["items"]}
    assert len(sent) == 5


def test_sync_index_only_sends_manifest_no_bytes(tmp_path: Path) -> None:
    config = _folder(tmp_path, "index_only")
    state = AgentState(tmp_path / "state.sqlite3")
    client = FakeClient()
    summary = asyncio.run(agent_ops.sync(config, state, client))
    assert summary["indexed"] == 1
    assert len(client.manifests) == 1
    assert client.manifests[0]["items"][0]["import_action"] == "index_only"
    assert client.extracted == [] and client.teleported == []


def test_sync_extract_action_uploads_for_extraction(tmp_path: Path) -> None:
    config = _folder(tmp_path, "index_and_extract")
    state = AgentState(tmp_path / "state.sqlite3")
    client = FakeClient()
    asyncio.run(agent_ops.sync(config, state, client))
    assert len(client.extracted) == 1
    assert client.teleported == []


def test_sync_extract_enqueue_failure_keeps_item_retryable(tmp_path: Path) -> None:
    """D7: when the server can't queue extraction the item stays retryable and is re-attempted.

    The item must NOT advance to the terminal "extracting" state, and a following sync must push it
    again even though the server optimistically reports "extracting".
    """
    config = _folder(tmp_path, "index_and_extract")
    state = AgentState(tmp_path / "state.sqlite3")

    # First sync: server stored the file but couldn't queue extraction (queue offline).
    client = FakeClient(extraction_queued=False)
    asyncio.run(agent_ops.sync(config, state, client))
    lid = state.all_files()[0].local_file_id
    assert client.extracted == [lid]
    rec = next(r for r in state.all_files() if r.local_file_id == lid)
    assert rec.processing_state == agent_ops.EXTRACT_QUEUE_FAILED  # not terminal → retryable

    # Second sync: the queue is back. Even though the server now reports "extracting", the local
    # retry marker forces another push, which this time succeeds and reaches the terminal state.
    client2 = FakeClient(
        my_files=[{"local_file_id": lid, "processing_state": "extracting"}],
        extraction_queued=True,
    )
    asyncio.run(agent_ops.sync(config, state, client2))
    assert client2.extracted == [lid]  # retried despite server's "extracting"
    rec = next(r for r in state.all_files() if r.local_file_id == lid)
    assert rec.processing_state == "extracting"


def test_sync_teleport_action_pushes_bytes(tmp_path: Path) -> None:
    config = _folder(tmp_path, "teleport")
    state = AgentState(tmp_path / "state.sqlite3")
    client = FakeClient()
    asyncio.run(agent_ops.sync(config, state, client))
    assert len(client.teleported) == 1


def test_sync_auto_fulfils_allow_requests(tmp_path: Path) -> None:
    config = _folder(tmp_path, "index_only", policy="allow")
    state = AgentState(tmp_path / "state.sqlite3")
    # First sync indexes the file so the state knows its id + policy + path.
    asyncio.run(agent_ops.sync(config, AgentState(tmp_path / "state.sqlite3"), FakeClient()))
    state = AgentState(tmp_path / "state.sqlite3")
    local_id = state.all_files()[0].local_file_id
    client = FakeClient(pending=[{"local_file_id": local_id}])
    summary = asyncio.run(agent_ops.sync(config, state, client))
    assert local_id in client.teleported
    assert summary["requests_fulfilled"] == 1


def test_blocked_request_is_rejected(tmp_path: Path) -> None:
    config = _folder(tmp_path, "index_only", policy="allow")
    state = AgentState(tmp_path / "state.sqlite3")
    asyncio.run(agent_ops.sync(config, state, FakeClient()))
    local_id = state.all_files()[0].local_file_id
    state.set_blocked(local_id, True)
    client = FakeClient(pending=[{"local_file_id": local_id}])
    asyncio.run(agent_ops.fulfil_requests(config, state, client))
    assert client.rejected == [local_id]
    assert client.teleported == []


def test_sync_caches_server_title_and_authors(tmp_path: Path) -> None:
    """Server-returned title/authors (#11) are cached in local state on sync."""
    config = _folder(tmp_path, "index_only")
    state = AgentState(tmp_path / "state.sqlite3")
    # First sync to learn the local_file_id.
    asyncio.run(agent_ops.sync(config, AgentState(tmp_path / "state.sqlite3"), FakeClient()))
    lid = state.all_files()[0].local_file_id
    client = FakeClient(
        my_files=[
            {
                "local_file_id": lid,
                "processing_state": "extracted",
                "extracted_title": "A Great Paper",
                "extracted_authors": "Ada Lovelace; Alan Turing",
            }
        ]
    )
    asyncio.run(agent_ops.sync(config, state, client))
    rec = next(r for r in state.all_files() if r.local_file_id == lid)
    assert rec.extracted_title == "A Great Paper"
    assert rec.extracted_authors == "Ada Lovelace; Alan Turing"


def test_request_teleport_offer_pushes_bytes(tmp_path: Path) -> None:
    """Agent-initiated teleport (#12) resolves the path locally and pushes the bytes."""
    config = _folder(tmp_path, "index_only")
    state = AgentState(tmp_path / "state.sqlite3")
    asyncio.run(agent_ops.sync(config, state, FakeClient()))
    lid = state.all_files()[0].local_file_id
    client = FakeClient()
    ok = asyncio.run(agent_ops.request_teleport_offer(state, client, lid))
    assert ok is True
    assert client.offered == [lid]
    assert (
        next(r for r in state.all_files() if r.local_file_id == lid).processing_state
        == "teleported"
    )


def test_request_teleport_offer_missing_file_returns_false(tmp_path: Path) -> None:
    state = AgentState(tmp_path / "state.sqlite3")
    assert asyncio.run(agent_ops.request_teleport_offer(state, FakeClient(), "nope")) is False


# --- Phase 1: truthful watched/unwatched/missing status model ---------------


def test_classify_watched_unwatched_missing(tmp_path: Path) -> None:
    watched_root = tmp_path / "papers"
    watched_root.mkdir()
    inside = watched_root / "a.pdf"
    inside.write_bytes(b"%PDF-1.4")
    outside = tmp_path / "loose.pdf"
    outside.write_bytes(b"%PDF-1.4")
    config = AgentConfig(folders=[ManagedFolder(path=watched_root)])

    assert agent_ops.classify(str(inside), config) == "watched"
    assert agent_ops.classify(str(outside), config) == "unwatched"
    assert agent_ops.classify(str(tmp_path / "nope.pdf"), config) == "missing"


def test_moved_out_of_watched_folder_is_unwatched_not_missing(tmp_path: Path) -> None:
    """A file moved on disk out of a watched folder is ``unwatched`` (still on disk), never missing."""
    root = tmp_path / "papers"
    root.mkdir()
    pdf = root / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4\nA")
    config = AgentConfig(folders=[ManagedFolder(path=root)])
    state = AgentState(tmp_path / "state.sqlite3")
    asyncio.run(agent_ops.sync(config, state, FakeClient()))
    lid = state.all_files()[0].local_file_id

    moved = tmp_path / "moved.pdf"
    pdf.rename(moved)
    # Point the index row at the new (still-on-disk) location, as a rescan would.
    rec = state.all_files()[0]
    state.upsert(
        local_file_id=lid, real_path=str(moved), sha256=rec.sha256, size_bytes=rec.size_bytes
    )
    assert agent_ops.classify(str(moved), config) == "unwatched"


def test_subset_scan_does_not_mark_other_roots_missing(tmp_path: Path) -> None:
    """Scanning only one enabled root must never flag files from another (still on disk) root."""
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    (root_a / "a.pdf").write_bytes(b"%PDF-1.4\nA")
    (root_b / "b.pdf").write_bytes(b"%PDF-1.4\nB")
    state = AgentState(tmp_path / "state.sqlite3")

    both = AgentConfig(folders=[ManagedFolder(path=root_a), ManagedFolder(path=root_b)])
    client = FakeClient()
    asyncio.run(agent_ops.sync(both, state, client))
    assert len(state.all_files()) == 2

    # Now disable root_b (subset scan of only root_a). b.pdf is still on disk → not missing.
    subset = AgentConfig(
        folders=[ManagedFolder(path=root_a), ManagedFolder(path=root_b, enabled=False)]
    )
    client2 = FakeClient()
    summary = asyncio.run(agent_ops.sync(subset, state, client2))
    assert summary["removed"] == 0
    assert client2.removed == []  # server NOT told anything was removed
    statuses = {agent_ops.classify(r.real_path, subset) for r in state.all_files()}
    assert statuses == {"watched", "unwatched"}


def test_report_source_removed_only_for_missing_not_unwatched(tmp_path: Path) -> None:
    """report_source_removed fires for disk-gone files, never for merely-unwatched ones."""
    root = tmp_path / "papers"
    root.mkdir()
    gone = root / "gone.pdf"
    gone.write_bytes(b"%PDF-1.4\nA")
    config = AgentConfig(folders=[ManagedFolder(path=root)])
    state = AgentState(tmp_path / "state.sqlite3")
    asyncio.run(agent_ops.sync(config, state, FakeClient()))
    lid = state.all_files()[0].local_file_id

    gone.unlink()  # truly deleted from disk
    client = FakeClient()
    summary = asyncio.run(agent_ops.sync(config, state, client))
    assert summary["removed"] == 1
    assert client.removed == [lid]


# --- Phase 2: prune (per-item + bulk) + unwatch preview ---------------------


def _two_root_state(tmp_path: Path):
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    (root_a / "a.pdf").write_bytes(b"%PDF-1.4\nA")
    (root_b / "b.pdf").write_bytes(b"%PDF-1.4\nB")
    both = AgentConfig(folders=[ManagedFolder(path=root_a), ManagedFolder(path=root_b)])
    state = AgentState(tmp_path / "state.sqlite3")
    asyncio.run(agent_ops.sync(both, state, FakeClient()))
    return root_a, root_b, both, state


def test_prune_unwatched_drops_only_unwatched(tmp_path: Path) -> None:
    root_a, root_b, both, state = _two_root_state(tmp_path)
    # Disable root_b so its file becomes unwatched (still on disk).
    subset = AgentConfig(
        folders=[ManagedFolder(path=root_a), ManagedFolder(path=root_b, enabled=False)]
    )
    pruned = agent_ops.prune_unwatched(subset, state)
    assert len(pruned) == 1
    remaining = state.all_files()
    assert len(remaining) == 1
    assert agent_ops.classify(remaining[0].real_path, subset) == "watched"


def test_files_unwatched_if_removed_previews_affected(tmp_path: Path) -> None:
    root_a, root_b, both, state = _two_root_state(tmp_path)
    affected = agent_ops.files_unwatched_if_removed(both, state, str(root_b))
    assert len(affected) == 1
    assert affected[0].real_path.endswith("b.pdf")
    # Removing a folder with no indexed files affects nothing.
    assert agent_ops.files_unwatched_if_removed(both, state, str(tmp_path / "nope")) == []


def test_managed_file_is_watched(tmp_path: Path) -> None:
    pdf = tmp_path / "solo.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    config = AgentConfig(files=[ManagedFile(path=pdf)])
    assert agent_ops.classify(str(pdf), config) == "watched"
    disabled = AgentConfig(files=[ManagedFile(path=pdf, enabled=False)])
    assert agent_ops.classify(str(pdf), disabled) == "unwatched"


def test_sync_auto_prune_toggle(tmp_path: Path) -> None:
    """auto_prune_unwatched (default OFF) prunes unwatched rows on a forward push when enabled."""
    root_a, root_b, both, state = _two_root_state(tmp_path)
    subset = AgentConfig(
        folders=[ManagedFolder(path=root_a), ManagedFolder(path=root_b, enabled=False)],
        auto_prune_unwatched=True,
    )
    summary = asyncio.run(agent_ops.sync(subset, state, FakeClient()))
    assert summary["pruned"] == 1
    assert len(state.all_files()) == 1


# --- Phase 3: reverse sync "Reconcile with server" + delete-on-disk guards --


def _server_known_file(tmp_path: Path, name: str = "a.pdf"):
    """A watched, indexed file marked server-known (teleported) — a reconcile un-index candidate."""
    root = tmp_path / "papers"
    root.mkdir(exist_ok=True)
    pdf = root / name
    pdf.write_bytes(b"%PDF-1.4\n" + name.encode())
    config = AgentConfig(folders=[ManagedFolder(path=root)])
    state = AgentState(tmp_path / "state.sqlite3")
    asyncio.run(agent_ops.sync(config, state, FakeClient()))
    lid = next(r.local_file_id for r in state.all_files() if r.real_path == str(pdf))
    state.set_processing_state(lid, "teleported")
    return root, pdf, config, state, lid


def test_reconcile_un_indexes_server_deleted(tmp_path: Path) -> None:
    root, pdf, config, state, lid = _server_known_file(tmp_path)
    # Server no longer lists it → candidate. Dry-run changes nothing; apply un-indexes it.
    preview = asyncio.run(agent_ops.reconcile(config, state, FakeClient(my_files=[]), apply=False))
    assert preview["would_un_index"] == 1 and preview["dry_run"] is True
    assert len(state.all_files()) == 1  # dry-run applied nothing

    applied = asyncio.run(agent_ops.reconcile(config, state, FakeClient(my_files=[]), apply=True))
    assert applied["un_indexed"] == 1
    assert state.all_files() == []


def test_reconcile_ignores_never_pushed_and_still_on_server(tmp_path: Path) -> None:
    root, pdf, config, state, lid = _server_known_file(tmp_path)
    # Still on the server → kept.
    kept = asyncio.run(
        agent_ops.reconcile(
            config, state, FakeClient(my_files=[{"local_file_id": lid}]), apply=True
        )
    )
    assert kept["un_indexed"] == 0
    # A never-pushed index_only row (state "indexed") is never a reconcile candidate.
    state.set_processing_state(lid, "indexed")
    r = asyncio.run(agent_ops.reconcile(config, state, FakeClient(my_files=[]), apply=True))
    assert r["would_un_index"] == 0 and state.all_files()


def test_delete_on_disk_boundary_rejects_outside_and_symlink_escape(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("PARACORD_AGENT_HOME", str(tmp_path))
    root, pdf, config, state, _ = _server_known_file(tmp_path)
    # An indexed, server-known file that lives OUTSIDE the watched folder.
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF-1.4\nO")
    state.upsert(local_file_id="outside", real_path=str(outside), sha256="o", size_bytes=1)
    state.set_processing_state("outside", "teleported")
    # A symlink INSIDE the watched folder that escapes to an outside target.
    escape_target = tmp_path / "escape.pdf"
    escape_target.write_bytes(b"%PDF-1.4\nE")
    link = root / "link.pdf"
    link.symlink_to(escape_target)
    state.upsert(local_file_id="escape", real_path=str(link), sha256="s", size_bytes=1)
    state.set_processing_state("escape", "teleported")

    assert agent_ops.is_strictly_inside_watched_folder(str(pdf), config) is True
    assert agent_ops.is_strictly_inside_watched_folder(str(outside), config) is False
    assert agent_ops.is_strictly_inside_watched_folder(str(link), config) is False

    agent_ops.arm_delete_on_disk(state)
    r = asyncio.run(
        agent_ops.reconcile(
            config, state, FakeClient(my_files=[]), delete_on_disk=True, apply=False
        )
    )
    deletable = {c["local_file_id"] for c in r["delete_candidates"]}
    assert deletable == {
        next(x.local_file_id for x in state.all_files() if x.real_path == str(pdf))
    }


def test_delete_on_disk_requires_arming(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PARACORD_AGENT_HOME", str(tmp_path))
    root, pdf, config, state, lid = _server_known_file(tmp_path)
    # Not armed → refused; nothing deleted or un-indexed.
    r = asyncio.run(
        agent_ops.reconcile(config, state, FakeClient(my_files=[]), delete_on_disk=True, apply=True)
    )
    assert r["refused"] is True and "arm" in r["reason"].lower()
    assert r["deleted"] == 0 and r["un_indexed"] == 0
    assert pdf.exists() and state.all_files()


def test_delete_on_disk_cap_refuses(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PARACORD_AGENT_HOME", str(tmp_path))
    monkeypatch.setattr(agent_ops, "MAX_DELETE_ON_DISK", 1)
    root, pdf1, config, state, _ = _server_known_file(tmp_path, "a.pdf")
    _, pdf2, _, _, _ = _server_known_file(tmp_path, "b.pdf")
    # Re-scan so both files are indexed + mark both server-known.
    asyncio.run(agent_ops.sync(config, state, FakeClient()))
    for rec in state.all_files():
        state.set_processing_state(rec.local_file_id, "teleported")

    agent_ops.arm_delete_on_disk(state)
    r = asyncio.run(
        agent_ops.reconcile(config, state, FakeClient(my_files=[]), delete_on_disk=True, apply=True)
    )
    assert r["refused"] is True and "> 1" in r["reason"]
    assert pdf1.exists() and pdf2.exists()  # no partial mass-delete
    assert len(state.all_files()) == 2  # nothing un-indexed on a refused delete run
    assert agent_ops.is_delete_on_disk_armed(state) is False  # still self-disabled


def test_delete_on_disk_moves_to_trash_and_self_disables(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PARACORD_AGENT_HOME", str(tmp_path))
    root, pdf, config, state, lid = _server_known_file(tmp_path)
    agent_ops.arm_delete_on_disk(state)
    assert agent_ops.is_delete_on_disk_armed(state) is True

    r = asyncio.run(
        agent_ops.reconcile(config, state, FakeClient(my_files=[]), delete_on_disk=True, apply=True)
    )
    assert r["deleted"] == 1 and r["un_indexed"] == 1
    assert not pdf.exists()  # moved off the original path
    trashed = list((tmp_path / "trash").iterdir())
    assert len(trashed) == 1 and trashed[0].name.endswith("a.pdf")  # recoverable, not unlinked
    assert state.all_files() == []
    assert agent_ops.is_delete_on_disk_armed(state) is False  # one-shot: auto-disabled


def test_scan_reuses_cached_hash_for_unchanged_files(tmp_path: Path, monkeypatch) -> None:
    """An unchanged file is not re-hashed on rescan (E7 incremental scan)."""
    config = _folder(tmp_path, "index_only")
    state = AgentState(tmp_path / "state.sqlite3")
    client = FakeClient()
    asyncio.run(agent_ops.sync(config, state, client))  # first scan: hashes + caches mtime

    calls = {"n": 0}
    real_hash = agent_ops.build_manifest_item.__globals__["hash_file"]

    def counting_hash(path, chunk_size=1024 * 1024):
        calls["n"] += 1
        return real_hash(path, chunk_size)

    monkeypatch.setattr("paperracks_agent.manifest.hash_file", counting_hash)
    # Rescan with the same (unchanged) file → cache hit, no hashing.
    agent_ops.scan_managed(config, state)
    assert calls["n"] == 0

    # Modify the file (content + mtime change) → it is re-hashed.
    pdf = tmp_path / "papers" / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4\nCHANGED CONTENT")
    import os

    os.utime(pdf, (pdf.stat().st_atime, pdf.stat().st_mtime + 10))
    agent_ops.scan_managed(config, state)
    assert calls["n"] == 1
