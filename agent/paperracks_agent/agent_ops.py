"""Agent operations (SPEC §32) shared by the CLI and the web GUI.

Pure-ish coordination on top of the server client + local config/state: scan managed items,
sync the manifest, apply the per-item import action (index_only / index_and_extract / teleport),
report removed sources, and handle server teleport requests (auto under an `allow` policy,
explicit approve/reject otherwise).
"""

from dataclasses import dataclass
from pathlib import Path

from paperracks_agent.client import PaRacORDServerClient
from paperracks_agent.config import AgentConfig
from paperracks_agent.manifest import build_manifest_item
from paperracks_agent.state import AgentState
from paperracks_agent.watcher import iter_pdf_files


@dataclass
class ScannedFile:
    local_file_id: str
    path: Path
    sha256: str
    size_bytes: int
    virtual_path: str
    action: str
    teleport_policy: str


def scan_managed(config: AgentConfig) -> list[ScannedFile]:
    """Scan all managed folders + files into a flat list of PDFs with their action/policy."""
    found: dict[str, ScannedFile] = {}
    for folder in config.folders:
        root = folder.path.expanduser().resolve(strict=False)
        if not root.exists():
            continue
        for path in iter_pdf_files([root]):
            item = build_manifest_item(path)
            try:
                rel = path.resolve().relative_to(root)
                virtual_path = f"{root.name}/{rel}"
            except ValueError:
                virtual_path = path.name
            found[item.local_file_id] = ScannedFile(
                local_file_id=item.local_file_id,
                path=path,
                sha256=item.sha256,
                size_bytes=item.size_bytes,
                virtual_path=virtual_path,
                action=folder.action,
                teleport_policy=folder.teleport_policy,
            )
    for managed in config.files:
        path = managed.path.expanduser()
        if path.exists() and path.suffix.lower() == ".pdf":
            item = build_manifest_item(path)
            found[item.local_file_id] = ScannedFile(
                local_file_id=item.local_file_id,
                path=path,
                sha256=item.sha256,
                size_bytes=item.size_bytes,
                virtual_path=path.name,
                action=managed.action,
                teleport_policy=managed.teleport_policy,
            )
    return list(found.values())


def _manifest_payload(scanned: list[ScannedFile]) -> dict:
    return {
        "items": [
            {
                "local_file_id": s.local_file_id,
                "sha256": s.sha256,
                "size_bytes": s.size_bytes,
                "display_path": s.path.name,
                "virtual_path": s.virtual_path,
                "import_action": s.action,
                "teleport_policy": s.teleport_policy,
            }
            for s in scanned
        ]
    }


async def sync(config: AgentConfig, state: AgentState, client: PaRacORDServerClient) -> dict:
    """Scan, send the manifest, apply per-file actions, report removals, fulfil allow-requests."""
    scanned = scan_managed(config)
    for s in scanned:
        state.upsert(
            local_file_id=s.local_file_id,
            real_path=str(s.path),
            sha256=s.sha256,
            size_bytes=s.size_bytes,
            virtual_path=s.virtual_path,
            import_action=s.action,
            teleport_policy=s.teleport_policy,
        )
    await client.send_manifest(_manifest_payload(scanned))

    absent = state.mark_absent_except({s.local_file_id for s in scanned})
    if absent:
        await client.report_source_removed(absent)

    try:
        server_state = {
            f["local_file_id"]: f.get("processing_state") for f in await client.get_my_files()
        }
    except Exception:  # noqa: BLE001 - status is best-effort; proceed without it
        server_state = {}

    applied = 0
    for s in scanned:
        pstate = server_state.get(s.local_file_id)
        if s.action == "index_and_extract" and pstate not in (
            "extracting",
            "extracted",
            "teleported",
        ):
            with s.path.open("rb") as handle:
                await client.upload_for_extraction(s.local_file_id, handle)
            state.set_processing_state(s.local_file_id, "extracting")
            applied += 1
        elif s.action == "teleport" and pstate != "teleported":
            with s.path.open("rb") as handle:
                await client.upload_teleport_content(s.local_file_id, handle)
            state.set_processing_state(s.local_file_id, "teleported")
            applied += 1

    fulfilled = await fulfil_requests(config, state, client)
    return {
        "indexed": len(scanned),
        "removed": len(absent),
        "actions_applied": applied,
        "requests_fulfilled": fulfilled,
    }


async def fulfil_requests(
    config: AgentConfig, state: AgentState, client: PaRacORDServerClient
) -> int:
    """Handle server teleport requests: auto-push under `allow`, auto-reject blocked, else leave."""
    pending = await client.get_pending_teleports()
    policy = {r.local_file_id: r.teleport_policy for r in state.all_files()}
    count = 0
    for entry in pending:
        local_file_id = entry["local_file_id"]
        if state.is_blocked(local_file_id):
            await client.reject_teleport(local_file_id)
            continue
        if policy.get(local_file_id) == "allow" and await approve_request(
            state, client, local_file_id
        ):
            count += 1
    return count


async def approve_request(
    state: AgentState, client: PaRacORDServerClient, local_file_id: str
) -> bool:
    """Push the bytes for a requested teleport (resolved locally by id). Returns True on success."""
    path = state.resolve_path(local_file_id)
    if path is None or not path.exists():
        return False
    with path.open("rb") as handle:
        await client.upload_teleport_content(local_file_id, handle)
    state.set_processing_state(local_file_id, "teleported")
    return True


async def reject_request(
    state: AgentState, client: PaRacORDServerClient, local_file_id: str, forever: bool
) -> dict:
    """Reject a request; `forever` records a local + server block."""
    if forever:
        state.set_blocked(local_file_id, True)
    return await client.reject_teleport(local_file_id, forever=forever)


async def unblock(state: AgentState, client: PaRacORDServerClient, local_file_id: str) -> dict:
    """Clear a reject-forever block locally and on the server."""
    state.set_blocked(local_file_id, False)
    return await client.unblock_teleport(local_file_id)
