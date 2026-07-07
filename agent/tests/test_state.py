"""Agent SQLite state store tests (§32 S3)."""

from __future__ import annotations

from pathlib import Path

from paperracks_agent.state import AgentState


def test_upsert_resolve_and_block(tmp_path: Path) -> None:
    state = AgentState(tmp_path / "state.sqlite3")
    state.upsert(
        local_file_id="h1",
        real_path="/home/me/papers/a.pdf",
        sha256="h1",
        size_bytes=10,
        virtual_path="papers/a.pdf",
        import_action="teleport",
        teleport_policy="allow",
    )
    assert state.resolve_path("h1") == Path("/home/me/papers/a.pdf")
    assert state.is_blocked("h1") is False
    state.set_blocked("h1", True)
    assert state.is_blocked("h1") is True

    rows = state.all_files()
    assert len(rows) == 1
    assert rows[0].teleport_policy == "allow"
    assert rows[0].import_action == "teleport"
    state.close()


def test_extracted_metadata_persists_and_is_not_wiped(tmp_path: Path) -> None:
    """Synced title/authors (#11) persist; a later plain rescan (no metadata) keeps them."""
    state = AgentState(tmp_path / "state.sqlite3")
    state.upsert(
        local_file_id="h1",
        real_path="/p/a.pdf",
        sha256="h1",
        size_bytes=10,
        extracted_title="Attention Is All You Need",
        extracted_authors="Vaswani; Shazeer",
    )
    rec = state.all_files()[0]
    assert rec.extracted_title == "Attention Is All You Need"
    assert rec.extracted_authors == "Vaswani; Shazeer"
    # A rescan-style upsert with no metadata must not clear the cached values.
    state.upsert(local_file_id="h1", real_path="/p/a.pdf", sha256="h1", size_bytes=10)
    rec = state.all_files()[0]
    assert rec.extracted_title == "Attention Is All You Need"
    assert rec.extracted_authors == "Vaswani; Shazeer"
    state.close()


def test_refresh_presence_tracks_on_disk_not_scan(tmp_path: Path) -> None:
    """``present`` = exists-on-disk: only truly-gone files go absent, and only once."""
    on_disk = tmp_path / "here.pdf"
    on_disk.write_bytes(b"%PDF-1.4")
    state = AgentState(tmp_path / "state.sqlite3")
    state.upsert(local_file_id="here", real_path=str(on_disk), sha256="h", size_bytes=1)
    state.upsert(
        local_file_id="gone", real_path=str(tmp_path / "gone.pdf"), sha256="g", size_bytes=1
    )

    newly_missing = state.refresh_presence()
    assert newly_missing == ["gone"]
    by_id = {r.local_file_id: r for r in state.all_files()}
    assert by_id["here"].present is True
    assert by_id["gone"].present is False
    # Idempotent: an already-missing file is not reported again.
    assert state.refresh_presence() == []
    state.close()


def test_forget_many_and_settings(tmp_path: Path) -> None:
    state = AgentState(tmp_path / "state.sqlite3")
    for i in (1, 2, 3):
        state.upsert(local_file_id=f"h{i}", real_path=f"/p/{i}.pdf", sha256=f"h{i}", size_bytes=1)
    assert state.forget_many(["h1", "h3", "nope"]) == 2
    assert {r.local_file_id for r in state.all_files()} == {"h2"}

    assert state.get_setting("k", "default") == "default"
    state.set_setting("k", "v")
    assert state.get_setting("k") == "v"
    state.set_setting("k", "v2")
    assert state.get_setting("k") == "v2"
    state.delete_setting("k")
    assert state.get_setting("k") is None
    state.close()
