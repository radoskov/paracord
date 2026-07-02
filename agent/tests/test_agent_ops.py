"""Agent operations tests (§32 S4) with a fake server client."""

from __future__ import annotations

import asyncio
from pathlib import Path

from paperracks_agent import agent_ops
from paperracks_agent.config import AgentConfig, ManagedFolder
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
