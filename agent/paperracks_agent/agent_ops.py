"""Agent operations (SPEC §32) shared by the CLI and the web GUI.

Pure-ish coordination on top of the server client + local config/state: scan managed items,
sync the manifest, apply the per-item import action (index_only / index_and_extract / teleport),
report removed sources, and handle server teleport requests (auto under an `allow` policy,
explicit approve/reject otherwise).
"""

import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from paperracks_agent.client import PaRacORDServerClient
from paperracks_agent.config import AgentConfig
from paperracks_agent.manifest import build_manifest_item
from paperracks_agent.state import AgentState, FileRecord
from paperracks_agent.watcher import iter_pdf_files

logger = logging.getLogger(__name__)

# Local processing state for an item the server stored but could NOT queue for extraction (its
# queue/Redis was down, so the extraction job was dropped). It is deliberately non-terminal so the
# next sync re-attempts the push, and it is surfaced in the agent GUI (D7).
EXTRACT_QUEUE_FAILED = "extract_queue_failed"

# Fallback import-batch cap when the server doesn't report one (older server or a failed /me): chunk
# conservatively at the server's out-of-the-box default so a large scan never trips the server cap.
DEFAULT_MAX_BATCH_ITEMS = 100

# Processing states that mean the server has (or had) a record of the file — the only files a
# reverse "reconcile" will un-index when they vanish from the server's view. A never-processed
# ``index_only`` row (state "indexed") is deliberately excluded so reconcile can never silently
# drop a purely-local file that was simply never pushed.
SERVER_KNOWN_STATES = frozenset({"extracting", "extracted", "teleported", EXTRACT_QUEUE_FAILED})

# Hard cap on the guarded reverse-sync delete-on-disk (owner requirement): a reconcile that would
# delete more than this many local files refuses to run — no partial mass-delete.
MAX_DELETE_ON_DISK = 100

# One-shot arming flag for the guarded delete-on-disk (stored in agent state settings).
DELETE_ARMED_KEY = "reconcile_delete_on_disk_armed"


def _watched_roots(config: AgentConfig) -> list[Path]:
    """Resolved paths of every *enabled* watched folder (non-strict; may not exist)."""
    roots: list[Path] = []
    for folder in config.folders:
        if folder.enabled:
            roots.append(folder.path.expanduser().resolve(strict=False))
    return roots


def is_watched(real_path: str, config: AgentConfig) -> bool:
    """Whether ``real_path`` sits under an enabled watched folder or is an enabled managed file."""
    resolved = Path(real_path).expanduser().resolve(strict=False)
    for root in _watched_roots(config):
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    for managed in config.files:
        if managed.enabled and resolved == managed.path.expanduser().resolve(strict=False):
            return True
    return False


def classify(real_path: str, config: AgentConfig) -> str:
    """Return the truthful status of an indexed file from two independent facts.

    ``on_disk`` (``stat`` exists) and ``watched`` (under an enabled watched root) yield:
    ``missing`` (gone from disk — the correct "file no longer on this workstation"),
    ``unwatched`` (on disk but outside every watched root — NOT "gone"), or ``watched`` (both).
    Computed fresh at display time so it never lags scan-membership.
    """
    if not Path(real_path).expanduser().exists():
        return "missing"
    return "watched" if is_watched(real_path, config) else "unwatched"


def _strict_resolve(path: Path) -> Path | None:
    """Fully resolve ``path`` (following symlinks) requiring it to exist; None on any failure."""
    try:
        return path.expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        return None


def is_strictly_inside_watched_folder(real_path: str, config: AgentConfig) -> bool:
    """Delete-on-disk safety bound: the file must resolve *strictly inside* an enabled watched
    folder, after following symlinks on both sides (so a symlink escape out of the folder is
    rejected). The folder root itself and any path outside every watched folder are not eligible.
    """
    resolved = _strict_resolve(Path(real_path))
    if resolved is None:
        return False
    for folder in config.folders:
        if not folder.enabled:
            continue
        root = _strict_resolve(folder.path)
        if root is None:
            continue
        try:
            rel = resolved.relative_to(root)
        except ValueError:
            continue
        if rel != Path("."):  # a real file inside the folder, not the folder root itself
            return True
    return False


def _trash_dir() -> Path:
    """Recoverable aside directory for delete-on-disk (never a hard unlink)."""
    import os

    env = os.environ.get("PARACORD_AGENT_HOME")
    base = Path(env).expanduser() if env else Path("~/.local/share/paracord-agent").expanduser()
    return base / "trash"


def _move_to_trash(path: Path) -> Path:
    """Move ``path`` into the recoverable trash dir under a collision-free, timestamped name."""
    trash = _trash_dir()
    trash.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d%H%M%S")
    dest = trash / f"{stamp}-{path.name}"
    counter = 1
    while dest.exists():
        dest = trash / f"{stamp}-{counter}-{path.name}"
        counter += 1
    shutil.move(str(path), str(dest))
    return dest


@dataclass
class ScannedFile:
    local_file_id: str
    path: Path
    sha256: str
    size_bytes: int
    virtual_path: str
    action: str
    teleport_policy: str
    mtime: float | None = None


def _cached_hash(cache: dict, path: Path) -> tuple[str | None, float]:
    """Return ``(known_hash_or_None, mtime)`` — reuse the cached content hash when the file's
    path/size/mtime are unchanged, so an unchanged PDF is not re-read+re-hashed (E7)."""
    stat = path.stat()
    entry = cache.get(str(path))
    if entry is not None:
        size, mtime, local_file_id = entry
        if size == stat.st_size and mtime == stat.st_mtime:
            return local_file_id, stat.st_mtime
    return None, stat.st_mtime


def scan_managed(config: AgentConfig, state: AgentState | None = None) -> list[ScannedFile]:
    """Scan all managed folders + files into a flat list of PDFs with their action/policy.

    When ``state`` is given, unchanged files (same path/size/mtime) reuse their cached content hash
    instead of being re-read — an incremental scan (E7).
    """
    cache = state.hash_cache() if state is not None else {}
    found: dict[str, ScannedFile] = {}
    for folder in config.folders:
        if not folder.enabled:
            continue
        root = folder.path.expanduser().resolve(strict=False)
        if not root.exists():
            continue
        for path in iter_pdf_files([root]):
            known, mtime = _cached_hash(cache, path)
            item = build_manifest_item(path, known_hash=known)
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
                mtime=mtime,
                virtual_path=virtual_path,
                action=folder.action,
                teleport_policy=folder.teleport_policy,
            )
    for managed in config.files:
        if not managed.enabled:
            continue
        path = managed.path.expanduser()
        if path.exists() and path.suffix.lower() == ".pdf":
            known, mtime = _cached_hash(cache, path)
            item = build_manifest_item(path, known_hash=known)
            found[item.local_file_id] = ScannedFile(
                local_file_id=item.local_file_id,
                path=path,
                sha256=item.sha256,
                size_bytes=item.size_bytes,
                mtime=mtime,
                virtual_path=path.name,
                action=managed.action,
                teleport_policy=managed.teleport_policy,
            )
    return list(found.values())


def folder_stats(config: AgentConfig) -> dict[str, dict]:
    """Per-folder filesystem stats for the GUI, keyed by the configured path string.

    Reports whether the path currently exists, the number of PDFs found (recursively), and the
    number of subdirectories — a cheap scan that lets the user see at a glance what a managed
    folder actually covers. Disabled folders are reported as ``enabled: False`` without scanning.
    """
    stats: dict[str, dict] = {}
    for folder in config.folders:
        key = str(folder.path)
        root = folder.path.expanduser().resolve(strict=False)
        if not folder.enabled:
            stats[key] = {"enabled": False, "exists": root.exists(), "pdfs": 0, "subfolders": 0}
            continue
        if not root.exists():
            stats[key] = {"enabled": True, "exists": False, "pdfs": 0, "subfolders": 0}
            continue
        pdfs = sum(1 for _ in iter_pdf_files([root]))
        subfolders = sum(1 for p in root.rglob("*") if p.is_dir())
        stats[key] = {"enabled": True, "exists": True, "pdfs": pdfs, "subfolders": subfolders}
    return stats


def _manifest_item(s: ScannedFile) -> dict:
    return {
        "local_file_id": s.local_file_id,
        "sha256": s.sha256,
        "size_bytes": s.size_bytes,
        "display_path": s.path.name,
        "virtual_path": s.virtual_path,
        "import_action": s.action,
        "teleport_policy": s.teleport_policy,
    }


def _manifest_payload(scanned: list[ScannedFile]) -> dict:
    return {"items": [_manifest_item(s) for s in scanned]}


async def _resolve_batch_cap(client: PaRacORDServerClient) -> int:
    """Fetch the server's import-batch cap (D1) so an oversized scan is split into ≤cap manifests.

    Falls back to the built-in default on any error or a server that doesn't report the field, so a
    reachable-but-old server (or a transient failure) never blocks a sync.
    """
    try:
        me = await client.get_me()
        cap = int(me.get("max_batch_items") or DEFAULT_MAX_BATCH_ITEMS)
    except Exception as exc:  # noqa: BLE001 - degrade to the conservative default
        logger.debug("Could not read server max_batch_items (%s); using default", exc)
        cap = DEFAULT_MAX_BATCH_ITEMS
    return max(1, cap)


async def _send_manifest_chunked(
    client: PaRacORDServerClient, scanned: list[ScannedFile], cap: int
) -> None:
    """Send the manifest in ≤cap chunks sequentially. An empty scan still sends one empty manifest
    so the server sees the agent currently has no files."""
    if not scanned:
        await client.send_manifest({"items": []})
        return
    for start in range(0, len(scanned), cap):
        chunk = scanned[start : start + cap]
        await client.send_manifest({"items": [_manifest_item(s) for s in chunk]})


async def sync(config: AgentConfig, state: AgentState, client: PaRacORDServerClient) -> dict:
    """Scan, send the manifest, apply per-file actions, report removals, fulfil allow-requests."""
    scanned = scan_managed(config, state)
    # Pull the server's view first so we can cache its title/authors metadata (#11) at upsert time.
    try:
        server_files = {f["local_file_id"]: f for f in await client.get_my_files()}
    except Exception:  # noqa: BLE001 - unknown server view; skip actions below, not re-upload all
        server_files = None
    for s in scanned:
        sf = server_files.get(s.local_file_id, {}) if server_files is not None else {}
        state.upsert(
            local_file_id=s.local_file_id,
            real_path=str(s.path),
            sha256=s.sha256,
            size_bytes=s.size_bytes,
            virtual_path=s.virtual_path,
            import_action=s.action,
            teleport_policy=s.teleport_policy,
            mtime=s.mtime,
            extracted_title=sf.get("extracted_title"),
            extracted_authors=sf.get("extracted_authors"),
        )
    cap = await _resolve_batch_cap(client)
    await _send_manifest_chunked(client, scanned, cap)

    # Re-stat every indexed file: ``present`` tracks existence-on-disk, independent of which roots
    # this scan covered. Only files that truly vanished from disk (missing) are reported to the
    # server as source-removed — merely-unwatched files (still on disk) are NOT (owner Q3).
    missing = state.refresh_presence()
    if missing:
        await client.report_source_removed(missing)

    # Forward auto-prune is opt-in (default OFF): keep-by-default holds, so a routine push never
    # silently drops unwatched-but-kept entries. When enabled, drop unwatched rows locally only
    # (no server contact — the server copy is independent).
    pruned = 0
    if getattr(config, "auto_prune_unwatched", False):
        pruned = len(prune_unwatched(config, state))

    applied = 0
    # Without the server's view we cannot tell what it already has, so skip the action phase
    # this cycle rather than re-uploading the whole corpus.
    if server_files is not None:
        server_state = {lid: f.get("processing_state") for lid, f in server_files.items()}
        local_state = {r.local_file_id: r.processing_state for r in state.all_files()}
        for s in scanned:
            pstate = server_state.get(s.local_file_id)
            # A prior push the server couldn't queue for extraction (its queue was offline) is
            # retryable: re-attempt it even though the server now reports "extracting" (D7).
            retry_extract = local_state.get(s.local_file_id) == EXTRACT_QUEUE_FAILED
            if s.action == "index_and_extract" and (
                pstate not in ("extracting", "extracted", "teleported") or retry_extract
            ):
                with s.path.open("rb") as handle:
                    resp = await client.upload_for_extraction(s.local_file_id, handle)
                if resp.get("extraction_queued", True):
                    state.set_processing_state(s.local_file_id, "extracting")
                    applied += 1
                else:
                    # Not fully processed — leave it retryable and surface it (the next sync retries).
                    state.set_processing_state(s.local_file_id, EXTRACT_QUEUE_FAILED)
                    logger.warning(
                        "Server stored %s but could not queue extraction (queue offline); "
                        "will retry next sync",
                        s.local_file_id,
                    )
            elif s.action == "teleport" and pstate != "teleported":
                with s.path.open("rb") as handle:
                    resp = await client.upload_teleport_content(s.local_file_id, handle)
                state.set_processing_state(s.local_file_id, "teleported")
                applied += 1
                if not resp.get("extraction_queued", True):
                    logger.warning(
                        "Teleported %s but the server could not queue extraction (queue offline); "
                        "the server recovery sweep will re-enqueue it",
                        s.local_file_id,
                    )

    fulfilled = await fulfil_requests(config, state, client)
    return {
        "indexed": len(scanned),
        "removed": len(missing),
        "pruned": pruned,
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


async def request_teleport_offer(
    state: AgentState, client: PaRacORDServerClient, local_file_id: str
) -> bool:
    """Agent-initiated teleport (#12): push a file's bytes to the server now.

    The real path is resolved **locally** by id (never sent to the server). Returns True on success.
    """
    path = state.resolve_path(local_file_id)
    if path is None or not path.exists():
        return False
    with path.open("rb") as handle:
        await client.offer_teleport(local_file_id, handle)
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


# --- prune / unwatch (Phase 2) ----------------------------------------------


def unwatched_ids(config: AgentConfig, state: AgentState) -> list[str]:
    """Ids of indexed files that are on disk but outside every enabled watched root."""
    return [
        r.local_file_id for r in state.all_files() if classify(r.real_path, config) == "unwatched"
    ]


def prune_unwatched(config: AgentConfig, state: AgentState) -> list[str]:
    """Drop every ``unwatched`` index row (on-disk file untouched, server not contacted). Returns ids."""
    ids = unwatched_ids(config, state)
    if ids:
        state.forget_many(ids)
    return ids


def _config_without(config: AgentConfig, target_path: str) -> AgentConfig:
    """A shallow config copy with the folder/file at ``target_path`` removed (for unwatch preview)."""
    target = str(Path(target_path).expanduser())
    return config.model_copy(
        update={
            "folders": [f for f in config.folders if str(f.path.expanduser()) != target],
            "files": [f for f in config.files if str(f.path.expanduser()) != target],
        }
    )


def files_unwatched_if_removed(
    config: AgentConfig, state: AgentState, target_path: str
) -> list[FileRecord]:
    """Indexed files (still on disk) that would become ``unwatched`` if ``target_path`` were removed.

    Simulates the config without that folder/file and returns rows currently ``watched`` that fall
    through to ``unwatched`` — the set the unwatch dialog offers to keep (default) or prune now.
    """
    after = _config_without(config, target_path)
    out: list[FileRecord] = []
    for rec in state.all_files():
        if (
            classify(rec.real_path, config) == "watched"
            and classify(rec.real_path, after) == "unwatched"
        ):
            out.append(rec)
    return out


# --- reverse sync: reconcile with the server (Phase 3) ----------------------


def arm_delete_on_disk(state: AgentState) -> None:
    """Arm the one-shot guarded delete-on-disk for the next reconcile (self-disables after a run)."""
    state.set_setting(DELETE_ARMED_KEY, "1")


def is_delete_on_disk_armed(state: AgentState) -> bool:
    return state.get_setting(DELETE_ARMED_KEY) == "1"


def disarm_delete_on_disk(state: AgentState) -> None:
    state.delete_setting(DELETE_ARMED_KEY)


def _row(rec: FileRecord) -> dict:
    return {
        "local_file_id": rec.local_file_id,
        "virtual_path": rec.virtual_path,
        "real_path": rec.real_path,
        "processing_state": rec.processing_state,
    }


async def reconcile(
    config: AgentConfig,
    state: AgentState,
    client: PaRacORDServerClient,
    *,
    delete_on_disk: bool = False,
    apply: bool = False,
) -> dict:
    """Reverse sync: compare the local index to the server and un-index files the server no longer has.

    Server-deleted candidates are locally-indexed files known to the server (``SERVER_KNOWN_STATES``)
    that are absent from ``get_my_files()`` now. ``delete_on_disk`` additionally moves each eligible
    file to the recoverable trash dir — but only files that pass every hard guard:

    * strictly inside a currently-watched folder (symlink escapes rejected),
    * the one-shot arm flag must be set (self-disables after this run),
    * a run that would delete > ``MAX_DELETE_ON_DISK`` files refuses entirely (no partial delete).

    With ``apply=False`` nothing is changed — it returns the dry-run preview the GUI shows first.
    """
    server_files = await client.get_my_files()
    server_ids = {f["local_file_id"] for f in server_files}
    candidates = [
        rec
        for rec in state.all_files()
        if rec.local_file_id not in server_ids and rec.processing_state in SERVER_KNOWN_STATES
    ]

    deletable: list[FileRecord] = []
    if delete_on_disk:
        deletable = [
            rec for rec in candidates if is_strictly_inside_watched_folder(rec.real_path, config)
        ]

    result: dict = {
        "dry_run": not apply,
        "delete_on_disk": delete_on_disk,
        "un_index_candidates": [_row(r) for r in candidates],
        "delete_candidates": [_row(r) for r in deletable],
        "would_un_index": len(candidates),
        "would_delete": len(deletable),
        "un_indexed": 0,
        "deleted": 0,
        "refused": False,
        "reason": None,
    }

    if delete_on_disk:
        if not is_delete_on_disk_armed(state):
            result["refused"] = True
            result["reason"] = "Delete-on-disk is not armed. Enable it right before reconciling."
        elif len(deletable) > MAX_DELETE_ON_DISK:
            result["refused"] = True
            result["reason"] = (
                f"Would delete {len(deletable)} files (> {MAX_DELETE_ON_DISK}). Refusing — "
                "delete these manually instead."
            )

    if not apply:
        return result

    if delete_on_disk and result["refused"]:
        # A refused delete-on-disk applies nothing (no un-index either) but still self-disables.
        disarm_delete_on_disk(state)
        return result

    deleted_ids: set[str] = set()
    if delete_on_disk:
        for rec in deletable:
            try:
                _move_to_trash(Path(rec.real_path))
                deleted_ids.add(rec.local_file_id)
            except OSError as exc:  # one unmovable file must not abort the whole run
                logger.warning("Could not move %s to trash: %s", rec.real_path, exc)
        disarm_delete_on_disk(state)  # one-shot: self-disable after the run

    un_indexed = state.forget_many([r.local_file_id for r in candidates])
    result["un_indexed"] = un_indexed
    result["deleted"] = len(deleted_ids)
    return result
