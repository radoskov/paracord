# Handoff — Batch A: local-agent overhaul (2026-07-07)

Agent-only. Committed on `main` (not pushed). Implements Batch A of
`docs/AGENT_OVERHAUL_DESIGN.md` (FINAL MODEL, owner-confirmed): no pinning, keep-by-default,
manual prune, per-item + bulk, forward "Scan & push" vs reverse "Reconcile with server". Five
commits, one per phase.

## Commits (on `main`)

- `8578a59` agent: truthful watched/unwatched/missing status model
- `893bba4` agent: per-item + bulk prune, unwatch keep/prune dialog, status filters
- `92f0e5e` agent: reverse-sync reconcile with guarded one-shot delete-on-disk
- `34dea70` agent: sync/reconcile/refresh feedback spinners + tooltip audit + auto-prune toggle
- `8d4235a` agent: CLI parity for reconcile, prune-unwatched, bulk forget

## Files changed

- `agent/paperracks_agent/state.py` — `present` = exists-on-disk; new `refresh_presence()` (replaces
  `mark_absent_except`), `forget_many()`, and a `settings` kv table (`get/set/delete_setting`) for
  the one-shot delete-arm flag. Idempotent `CREATE TABLE settings` added to `_SCHEMA` (safe on a
  pre-existing DB; no destructive ALTER).
- `agent/paperracks_agent/agent_ops.py` — `is_watched` / `classify`; `is_strictly_inside_watched_folder`
  (strict symlink-resolving boundary); `unwatched_ids` / `prune_unwatched` / `files_unwatched_if_removed`;
  `arm/disarm/is_delete_on_disk_armed`; `reconcile(...)`; trash-dir move. `sync` now uses
  `refresh_presence` (reports only missing) + honours `auto_prune_unwatched`.
- `agent/paperracks_agent/config.py` — `auto_prune_unwatched: bool = False`.
- `agent/paperracks_agent/web.py` — new endpoints `/api/unwatch-preview`, `/api/prune-unwatched`,
  `/api/bulk`, `/api/reconcile`, `/api/reconcile/arm-delete`, `/api/set-auto-prune`; `/api/remove`
  gained `prune_ids`; `/api/files` returns `status`; GUI rework (badges, filters, multi-select bulk
  bar, per-item prune, reconcile + unwatch modals, Scan & push / Reconcile / Refresh feedback
  spinners, tooltip pass, auto-prune toggle).
- `agent/paperracks_agent/cli.py` — `reconcile`, `prune-unwatched`, `forget <id>...` subcommands.
- Tests: `test_state.py`, `test_agent_ops.py`, `test_web.py`, new `test_cli.py`.

## Status model (the bug fix)

Two independent facts per indexed file: **on_disk** (`stat`) and **watched** (`real_path` under an
enabled watched root/file). `classify` → `watched` / `unwatched` ("on disk, not in a watched
folder") / `missing` ("file no longer on this workstation"). The old bug (`mark_absent_except` set
`present=0` for anything not in the last scan) is removed: `refresh_presence` re-stats every file, so
a subset scan never marks other roots missing and an unwatched file is never mislabelled "gone".
`report_source_removed` fires only for truly-missing files.

## Delete-on-disk guards (safety-critical — all enforced in `agent_ops.reconcile`)

- **Boundary**: `is_strictly_inside_watched_folder` resolves symlinks with `resolve(strict=True)` on
  both file and root; only files strictly inside an enabled watched folder are eligible (folder root
  itself and symlink escapes rejected). Tested: `test_delete_on_disk_boundary_rejects_outside_and_symlink_escape`.
- **Two dialogs / arm flag**: enable requires the arm flag (GUI dialog 1 → `/api/reconcile/arm-delete`;
  CLI `--confirm-delete`); the review list (GUI dialog 2 modal / CLI dry-run) shows every full path.
  Not armed → refused. Tested: `test_delete_on_disk_requires_arming`, `test_cli_reconcile_delete_needs_confirm`.
- **Hard cap 100** (`MAX_DELETE_ON_DISK`): a run over the cap refuses entirely, deletes/un-indexes
  nothing. Tested: `test_delete_on_disk_cap_refuses` (cap monkeypatched to 1).
- **One-shot self-disable**: disarmed after any apply run (including refused). Tested in the cap and
  trash tests.
- **Recoverable trash**: `_move_to_trash` moves to `$PARACORD_AGENT_HOME/trash` (never `unlink`).
  Tested: `test_delete_on_disk_moves_to_trash_and_self_disables`.

## Assumptions / decisions

- **Prune/forget never contact the server** (matches existing `forget` + owner Q3 + the unwatch
  dialog "no server contact"). The spec phrase "report_source_removed for … explicitly pruned" is
  interpreted as **not** applying to unwatched-file prune; only disk-gone files are reported. This
  keeps "server copy is independent" and keep-by-default intact.
- **Reconcile un-index scope**: only files whose local `processing_state` is server-known
  (`extracting`/`extracted`/`teleported`/`extract_queue_failed`) and absent from `get_my_files()`.
  A never-pushed `index_only` row is never dropped by reconcile (prevents silently deleting a
  purely-local file). Matches the owner's "process on server → delete on server → drop locally" flow.
- Unwatch prune-now targets only the specific ids the dialog listed (not all unwatched), so unrelated
  kept-unwatched files are never collateral.
- `config/agent.example.yaml` is a stale/aspirational nested schema not loaded by `load_config`
  (flat pydantic model); the new `auto_prune_unwatched` key was not added there to avoid implying a
  structure the loader doesn't use.

## Tests

Agent suite 34 → 58, all green (`make test-agent` / `test-agent-full`). Added: status classification
(watched/unwatched/missing, moved-on-disk → unwatched, subset scan not marking others missing,
report-only-missing), prune (per-item + bulk), unwatch preview + prune-now, auto-prune toggle,
reconcile un-index of server-deleted + the four delete-on-disk guards, CLI subcommands. `ruff
check/format agent` clean. GUI JS syntax validated with `node --check`; served page contains all new
hooks.

## Security implications

- Delete-on-disk is the only new destructive local action; all five guards above gate it and it is
  loopback + token-gated like the rest of the GUI. No server-supplied path is ever accepted; all
  id→path resolution stays local (`state.resolve_path`).
- Bulk actions reuse existing per-id server calls; no new server endpoints or privileges.

## Next recommended task

Consider a small "empty trash" / trash-listing affordance for the delete-on-disk aside dir (files
accumulate silently), and an optional server-side audit event when reconcile un-indexes (currently
purely local).
