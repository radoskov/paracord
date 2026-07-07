# PaRacORD — Local-agent overhaul design (2026-07-06)

A usability overhaul of the workstation agent's **index lifecycle, sync semantics, and local web
GUI**, driven by concrete problems the owner hit while refreshing the index (removing watched
folders + re-syncing). This is a design/proposal doc to validate; a workplan + implementation
follow once agreed.

## How the agent works today (grounded in the code)
- **State** (`agent/paperracks_agent/state.py`): a SQLite `files` table keyed by `local_file_id`
  (content hash) → `real_path` (local-only), `sha256`, `size/mtime`, `import_action`
  (`index_only`/`index_and_extract`/`teleport`), `teleport_policy`, `processing_state`,
  `teleport_blocked`, and a single **`present`** boolean.
- **Scan** (`agent_ops.scan_managed`): walks the *enabled* watched folders + files
  (`config.folders`/`config.files`), hashing PDFs (incremental via `hash_cache`).
- **Sync** (`agent_ops.sync`): scans → upserts each found file (`present=1`) → sends the manifest to
  the server → **`state.mark_absent_except({scanned ids})`** sets `present=0` for every indexed
  file NOT in this scan and **reports those ids to the server as "source removed"** → applies
  per-file actions (extract/teleport) using the server's view.
- **GUI** (`web.py`): an *Indexed* tab (from `state.all_files()`), a *Files & folders* tab
  (manage watched folders/files), and a single-item **`forget`** (delete one index row; on-disk
  file untouched).

## The problems

**P1 — "(file no longer on this workstation)" is wrong for merely-unwatched files.** `present` means
"seen in the last scan," not "exists on disk." Remove a watched folder → its files aren't scanned →
`mark_absent_except` sets `present=0` → the GUI shows "no longer on this workstation," even though the
file is still on disk (just outside any watched folder). The agent conflates two independent facts:
*is the file on disk?* and *is it under a watched root?*

**P2 — over-signalling the server on unwatch.** The same path calls
`report_source_removed(absent)`, so removing a watched folder tells the *server* those files were
removed — when they still exist locally and were merely unwatched.

**P3 — no way to prune index entries that are no longer watched.** Once a folder is removed, its rows
linger (mislabelled) with no bulk way to drop them from the index.

**P4 — no reverse sync (server → agent).** There's no "reconcile with the server" that removes
locally-indexed files the *server* no longer has. Owner's workflow: add a local paper → process it
on the server → decide it doesn't fit → delete it on the server → want it removed locally too
(un-indexed, optionally deleted on disk). Today nothing does this.

**P5 — no bulk operations.** `forget` is one-at-a-time; removing many stale entries is tedious.

## Proposed redesign

### 1. A truthful, computed file status (fixes P1)
Stop overloading `present`. Derive each indexed file's status from **two independent checks** at
display/scan time:
- **on disk?** — `stat(real_path)` exists (cheap; already have the path).
- **watched?** — `real_path` is under an *enabled* watched folder/file in the current config.

Yielding a clear status vocabulary:
| Status | on disk | watched | Meaning / label |
|---|---|---|---|
| **watched** | ✓ | ✓ | Normal — tracked and monitored. |
| **unwatched** | ✓ | ✗ | *"On disk, but not in a watched folder"* (the owner's case) — NOT "gone". |
| **missing** | ✗ | — | *"File no longer on this workstation"* — the correct use of that label. |
Plus the existing orthogonal facets shown alongside: `processing_state` (indexed / extracting /
extracted / teleported / extract_queue_failed), `teleport_blocked`, `import_action`.

Implementation: keep `real_path`; compute `on_disk` by `stat` and `watched` by comparing against
config roots (a helper `classify(record, config)`), surfaced in `all_files()`/the GUI. `present`
becomes "exists on disk" (set from `stat`, not from scan-membership), so a rescan of a subset never
marks other files "missing".

### 2. Don't tell the server a file is "removed" just because it's unwatched (fixes P2)
Only `report_source_removed` when a file is **truly gone from disk** (status → missing) or the user
**explicitly prunes/un-indexes** it. Unwatching alone changes local tracking, not the server's view.
(A file staying on the server after you stop watching it locally is correct — the server copy/record
is independent.)

### 3. Prune unwatched entries from the index (fixes P3)
- A **"Remove unwatched from index"** action (bulk + per-item): drops index rows whose status is
  `unwatched` (on disk, outside all watched roots). The on-disk file is untouched.
- Optional setting **"Auto-prune unwatched on scan"** (default OFF) for users who want the index to
  strictly mirror the watched set.

### 4. Reconcile with the server — optional reverse sync (fixes P4)
An explicit **"Reconcile with server"** operation (button + CLI), separate from the routine push
sync, that compares the local index against `get_my_files()` and offers, each independently toggled:
- **Un-index files the server no longer has** — for locally-indexed files that were known to the
  server (previously teleported/extracting) but are absent from `get_my_files()` now (i.e. deleted
  on the server) → remove them from the local index. This is the owner's "delete on server → drops
  locally" flow. Default action of the reconcile.
- **Also delete the local file from disk** — a stronger, explicitly-confirmed opt-in. Scoped for
  safety to files the **agent itself teleported/manages** (never an arbitrary user file it merely
  indexed), and it moves the file to a local *trash/aside* dir rather than hard-deleting, so it's
  recoverable. OFF by default.
- **Dry-run preview** — the reconcile first shows exactly what it will do ("N un-index, M delete on
  disk, K unwatched to prune") and applies only on confirm.

### 5. Bulk operations + a usable Indexed tab (fixes P5, + general usability)
- **Multi-select** in the Indexed tab with bulk actions: Forget, Prune-unwatched, Teleport now,
  Block, Unblock, Re-extract. "Select all / select filtered."
- **Filters + sort + search**: by status (watched/unwatched/missing), by processing state, by
  teleport/blocked, by source folder; free-text on title/path. So a user can "select all unwatched →
  forget" in two clicks.
- **Status badges + counts** at the top: e.g. `142 watched · 8 unwatched · 3 missing · 12 teleported
  · 2 blocked`, each a filter.
- **Per-file provenance**: which watched folder it came from, last scanned, server processing state,
  teleported?/blocked?.

## Additional usability improvements (proposed while we're here)
- **Reconcile/sync UX**: distinct, clearly-labelled buttons — "Scan & push" (routine) vs "Reconcile
  with server" (the two-way, preview-first one) — instead of one opaque sync.
- **Safety**: on-disk deletions always go to a recoverable *aside* dir; destructive actions preview
  first; forgetting is reversible on the next scan if the file is still watched.
- **GUI refresh consistency**: every action (forget, prune, reconcile, add/remove folder)
  immediately refreshes the Indexed list + the counts (no manual reload) — mirrors the server-side
  refresh work.
- **Empty/removed watched folder feedback**: the Files & folders tab already shows per-folder stats;
  add a warning when a removed/renamed folder leaves N now-unwatched indexed files, with a one-click
  "prune them" / "keep as unwatched".
- **CLI parity**: expose reconcile / prune-unwatched / bulk-forget as CLI subcommands too (the agent
  is scriptable/headless).

## Open questions to validate (owner)
1. **On-disk delete on reconcile** — un-index only by default, with delete-on-disk as an
   explicitly-confirmed opt-in that moves to a trash dir and is limited to agent-managed/teleported
   files? (My recommendation.) Or do you want delete-on-disk to cover *any* indexed file?
2. **Unwatching a folder** — keep its files in the index as **unwatched** (my rec: don't auto-remove,
   offer easy prune), auto-prune them, or prompt each time?
3. **Server signalling on unwatch** — agreed that merely unwatching should NOT tell the server the
   file was removed (only true disk-deletion / explicit prune does)?
4. **Reconcile default** — should "Reconcile with server" default to also pruning unwatched, or keep
   the three toggles (un-index server-deleted / prune unwatched / delete-on-disk) independent with
   safe defaults (only "un-index server-deleted" on)?

Once you validate these, I'll turn this into a phased workplan (status model + GUI vocabulary →
prune + bulk actions → reconcile/reverse-sync with preview → CLI parity + polish) and implement it.

---

## Validated decisions (owner, 2026-07-07)

**Q1 — delete-on-disk on reconcile: approved WITH hard safety guards.** In addition to the base rec
(opt-in, moves to a recoverable trash/aside dir), the auto-delete must:
- **Never cross the watched-folder boundary.** Only files whose `real_path` resolves *strictly
  inside a currently-watched folder* are ever eligible (no symlink escape, no unwatched/arbitrary
  path). This is a hard safety bound, enforced before anything is touched.
- **Two dialogs:** (1) a confirmation popup to *enable* the feature; (2) a review dialog listing
  **every to-be-deleted file with full path + name** before it runs — sized so the whole list fits
  in the message.
- **Hard cap: 100 files.** If a reconcile would delete more than 100, the feature **refuses to run**
  and tells the user to delete those files manually. (No partial mass-delete.)
- **One-shot, self-disabling.** The user enables it right before a reverse sync; it processes that
  one run, then **auto-disables**. Using it again requires re-enabling. The enable dialog must state
  this clearly ("this applies to the next reconcile only, then turns itself off").

**FINAL MODEL (owner-confirmed 2026-07-07) — no pinning; keep-by-default; manual prune.** The
earlier "pin" idea is DROPPED (it only patched a contradiction caused by auto-pruning by default):
- **Unwatch a folder** → confirmation dialog. Default: the files become **unwatched but kept in the
  index** (clearly labelled `unwatched`). The dialog offers an optional checkbox **"also remove
  these from the index now"** (prune locally). Never contacts the server (Q3).
- **Pruning is a MANUAL action, not automatic.** Forward sync ("Scan & push") does **NOT** auto-prune
  by default — it's a toggle **default OFF**, so "keep by default" always holds and nothing silently
  removes kept files. A separate **"Prune unwatched"** bulk button (Indexed tab) removes unwatched
  entries when the user chooses; every row also has its own per-item **Prune** (and **Forget**).
- Unwatched files can also arise without unwatching a folder (e.g. a file **moved on disk** out of a
  watched folder) — they simply show as `unwatched` and are handled by the same manual prune. No
  special state, no pin, no unpin.

**Q3 — unwatching does NOT signal the server.** Confirmed: merely unwatching never tells the server
a file was removed (only true disk-deletion / explicit prune / server-side deletion does).

**Q4 — two separate operations.** **Forward "Scan & push"** (auto-prune toggle default OFF) vs
**Reverse "Reconcile with server"** (removes server-deleted; does not touch unwatched).

**Per-item AND bulk (owner-confirmed).** Every indexed row keeps its own actions — **Forget**,
**Prune** (if unwatched), **Teleport now**, **Block/Unblock**, **Re-extract** — and multi-select
"apply to selected" is an *additional* faster path, never the only option.

**Additional (owner 2026-07-07) — GUI feedback + tooltips.**
- **Sync/Refresh feedback.** Both currently "just happen" with no indication. Add visible feedback: a
  spinner/mini-animation on the button while running + a concise status message on completion
  (e.g. "Synced: 3 pushed, 1 pruned" / "Nothing to do" / "Reconciled: 2 un-indexed"). Applies to
  Scan & push, Reconcile, and Refresh.
- **Tooltips.** Audit every button's hover-help across the agent GUI so it's present, accurate, and
  descriptive (they've drifted / are missing in places).

Fully settled — no open questions. Phased build: (1) truthful watched/unwatched/missing status model
+ GUI vocabulary; (2) per-item + bulk Forget/Prune + the unwatch keep/prune-now dialog + manual
"Prune unwatched"; (3) reverse-sync "Reconcile with server" (un-index server-deleted + the guarded
one-shot delete-on-disk + dry-run preview) vs forward "Scan & push" (auto-prune toggle default OFF);
(4) sync/refresh feedback + tooltip audit; (5) CLI parity. This is Batch A, built after Batches
P/C/S/D (all complete).
