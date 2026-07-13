"""Version-tolerant logical export/backup + restore (feature batch 2026-07-13, item 1).

Why not just pg_dump (``make backup``)? A SQL dump is bound to the exact schema it was taken
from — one migration later it may not restore at all. This exporter writes a **logical, self-
describing archive** designed to survive code/schema evolution and to degrade by *dropping the
least data possible* rather than failing:

* one JSONL file per table, each row a plain ``{column: value}`` dict (UUIDs/datetimes ISO-encoded);
* a ``manifest.json`` with the format version, alembic revision, hash algorithm, and row counts;
* optionally ``pdfs/<sha256>.pdf`` — PDFs are content-addressed by **SHA-256** (deterministic and
  machine-independent, so no seed/key needs to travel; the manifest still records the algorithm).

Restore (owner-only) maps rows by **column-name intersection** against the *current* schema:

* new columns → backfilled by the model/database defaults;
* deleted columns → their values dropped (counted);
* renamed columns → translated via the ``_RENAMES`` registry (maintained forward as migrations
  rename things);
* rows that still can't be inserted (missing required values, unique/FK conflicts) → skipped and
  counted, inside per-row SAVEPOINTs so one bad row never poisons the batch.

Modes: **merge** (insert rows whose primary key doesn't exist; never touches existing rows) and
**replace** (wipe the exported tables first; the current owner account is re-inserted afterwards
if the backup didn't bring one, so a restore can't lock the owner out — and the server-console
password reset remains the backstop).

PDF pairing: after the rows land, every restored File row must be backed by a real PDF — from the
archive's ``pdfs/`` folder or a scanned server import root (never an arbitrary path; global rule
3). A File whose sha256 has no matching PDF is deleted with its links, exactly as if the user had
removed the file manually — the paper record itself survives.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import shutil
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Table, delete, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401 - registers every model on Base.metadata
from app.core.config import Settings, get_settings
from app.db.base import Base
from app.services.audit import record_event
from app.services.storage import content_addressed_path

logger = logging.getLogger(__name__)

FORMAT_VERSION = 1
HASH_ALGORITHM = "sha256"

# Transient/per-session state that must not travel between systems.
_EXCLUDED_TABLES = {
    "user_sessions",  # login sessions are machine-local
    "import_staging_batches",  # in-flight import previews (large TEI blobs, meaningless later)
    "import_staging_items",
}

# Column renames across schema history: {table: {backup_column: current_column}}. Extend this map
# whenever a migration renames/moves a column so old backups keep restoring losslessly.
_RENAMES: dict[str, dict[str, str]] = {}

# Restore order safety: replace-mode deletes run over sorted_tables in REVERSE (children first);
# inserts run in forward order (parents first).


def backups_dir(settings: Settings | None = None) -> Path:
    """The archive directory: a sibling of the managed library (same persisted volume)."""
    settings = settings or get_settings()
    root = Path(settings.managed_library_root).expanduser().resolve().parent / "backups"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _exportable_tables() -> list[Table]:
    return [t for t in Base.metadata.sorted_tables if t.name not in _EXCLUDED_TABLES]


def _encode_value(value):
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _decode_value(column, value):
    """Coerce a JSON-decoded value back to the column's Python type (best-effort)."""
    if value is None:
        return None
    try:
        python_type = column.type.python_type
    except NotImplementedError:
        return value
    if python_type is uuid.UUID and isinstance(value, str):
        return uuid.UUID(value)
    if python_type is datetime and isinstance(value, str):
        return datetime.fromisoformat(value)
    return value


# --------------------------------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------------------------------


def _alembic_revision(db: Session) -> str | None:
    try:
        from sqlalchemy import text

        return db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:  # noqa: BLE001 - table may not exist (fresh test DBs)
        return None


def create_backup(
    db: Session,
    *,
    include_pdfs: bool,
    settings: Settings | None = None,
    actor_user_id: uuid.UUID | None = None,
) -> dict:
    """Write a backup archive; returns ``{archive, path, manifest}``.

    PDFs are resolved through the normal file resolver (managed library or server roots) and
    stored content-addressed as ``pdfs/<sha256>.pdf``; unresolvable files are listed in the
    manifest rather than failing the export.
    """
    from app.models.file import File
    from app.services.file_paths import resolve_backend_readable_pdf_path

    settings = settings or get_settings()
    created_at = datetime.now(UTC)
    name = f"paracord-backup-{created_at.strftime('%Y%m%d-%H%M%S')}.zip"
    path = backups_dir(settings) / name

    counts: dict[str, int] = {}
    missing_pdfs: list[str] = []
    pdf_count = 0
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for table in _exportable_tables():
            buffer = io.StringIO()
            rows = 0
            for row in db.execute(select(table)).mappings():
                buffer.write(
                    json.dumps({k: _encode_value(v) for k, v in row.items()}, ensure_ascii=False)
                )
                buffer.write("\n")
                rows += 1
            counts[table.name] = rows
            archive.writestr(f"tables/{table.name}.jsonl", buffer.getvalue())
        if include_pdfs:
            for file in db.scalars(select(File)).all():
                try:
                    pdf_path = resolve_backend_readable_pdf_path(db, file=file, settings=settings)
                    archive.write(pdf_path, f"pdfs/{file.sha256}.pdf")
                    pdf_count += 1
                except Exception:  # noqa: BLE001 - an unresolvable PDF must not fail the backup
                    missing_pdfs.append(file.sha256)
        manifest = {
            "format_version": FORMAT_VERSION,
            "created_at": created_at.isoformat(),
            "alembic_revision": _alembic_revision(db),
            "hash_algorithm": HASH_ALGORITHM,
            "include_pdfs": include_pdfs,
            "pdf_count": pdf_count,
            "pdfs_unresolved": missing_pdfs,
            "tables": counts,
            "notes": "Per-user UI preferences (YAML files) are not included.",
        }
        archive.writestr("manifest.json", json.dumps(manifest, indent=2))
    record_event(
        db,
        "backup.created",
        actor_user_id=actor_user_id,
        entity_type="backup",
        entity_id=name,
        details={"include_pdfs": include_pdfs, "tables": sum(counts.values()), "pdfs": pdf_count},
    )
    return {"archive": name, "path": str(path), "manifest": manifest}


# --------------------------------------------------------------------------------------------------
# Analyze (the dry-run shown to the owner before restoring)
# --------------------------------------------------------------------------------------------------


def analyze_backup(path: Path) -> dict:
    """Read an archive's manifest and diff its tables/columns against the current schema.

    This is the pre-restore report: which tables are unknown now (their rows would be dropped),
    which columns would be dropped, and which current columns the backup lacks (backfilled by
    defaults). Never modifies anything.
    """
    current = {t.name: t for t in Base.metadata.tables.values()}
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        report = []
        for member in archive.namelist():
            if not member.startswith("tables/") or not member.endswith(".jsonl"):
                continue
            name = member[len("tables/") : -len(".jsonl")]
            first_line = archive.read(member).split(b"\n", 1)[0]
            backup_columns = set(json.loads(first_line)) if first_line.strip() else set()
            renames = _RENAMES.get(name, {})
            backup_columns = {renames.get(c, c) for c in backup_columns}
            table = current.get(name)
            entry = {
                "table": name,
                "rows": manifest.get("tables", {}).get(name, 0),
                "unknown_table": table is None,
                "dropped_columns": sorted(backup_columns - set(table.columns.keys()))
                if table is not None
                else sorted(backup_columns),
                "new_columns": sorted(set(table.columns.keys()) - backup_columns)
                if table is not None and backup_columns
                else [],
            }
            report.append(entry)
    return {"manifest": manifest, "tables": sorted(report, key=lambda e: e["table"])}


# --------------------------------------------------------------------------------------------------
# Restore
# --------------------------------------------------------------------------------------------------


def _pdf_map_from_root(root: Path) -> dict[str, Path]:
    """Scan a folder recursively, hashing every ``*.pdf`` → {sha256: path} (deterministic)."""
    mapping: dict[str, Path] = {}
    for pdf in sorted(root.rglob("*.pdf")):
        try:
            digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
        except OSError:
            continue
        mapping.setdefault(digest, pdf)
    return mapping


def _resolve_pdf_root(db: Session, alias: str, settings: Settings) -> Path:
    """Resolve a configured import-root alias to a directory (never an arbitrary path)."""
    from app.services.import_roots import list_merged_roots

    for entry in list_merged_roots(db, settings):
        if entry["alias"] == alias and entry["exists"]:
            return Path(entry["path"])
    raise ValueError(f"Unknown or missing import root alias: {alias!r}")


def _snapshot_owner(db: Session) -> dict | None:
    from app.models.user import User

    users = Base.metadata.tables["users"]
    row = db.execute(select(users).where(users.c.role == "owner")).mappings().first()
    _ = User  # imported for model registration clarity
    return dict(row) if row else None


def restore_backup(
    db: Session,
    *,
    path: Path,
    mode: str,
    pdf_root_alias: str | None = None,
    settings: Settings | None = None,
    actor_user_id: uuid.UUID | None = None,
) -> dict:
    """Restore an archive in ``merge`` or ``replace`` mode. Returns a per-table summary.

    The prime directive: load as much as safely possible, never corrupt the database. Every row
    inserts inside its own SAVEPOINT; anything that conflicts is counted and skipped. The caller
    commits.
    """
    if mode not in ("merge", "replace"):
        raise ValueError("mode must be 'merge' or 'replace'")
    settings = settings or get_settings()

    owner_snapshot = _snapshot_owner(db) if mode == "replace" else None
    summary: dict = {
        "mode": mode,
        "tables": {},
        "files_paired": 0,
        "files_dropped": 0,
        "owner_reinserted": False,
    }

    with zipfile.ZipFile(path) as archive:
        members = set(archive.namelist())

        if mode == "replace":
            for table in reversed(_exportable_tables()):
                db.execute(delete(table))
            db.flush()

        restored_file_ids: list[uuid.UUID] = []
        for table in _exportable_tables():
            member = f"tables/{table.name}.jsonl"
            if member not in members:
                continue  # older backup without this table — new tables start empty
            renames = _RENAMES.get(table.name, {})
            valid_columns = set(table.columns.keys())
            pk_columns = [c.name for c in table.primary_key.columns]
            stats = {"inserted": 0, "skipped_existing": 0, "skipped_conflict": 0}
            dropped_columns: set[str] = set()
            retry_rows: list[dict] = []

            def _try_insert(row: dict, table: Table = table) -> bool:
                try:
                    with db.begin_nested():
                        db.execute(table.insert().values(**row))
                except Exception:  # noqa: BLE001 - unique/FK/NOT NULL conflict
                    return False
                return True

            for line in archive.read(member).decode("utf-8").splitlines():
                if not line.strip():
                    continue
                raw = json.loads(line)
                row: dict = {}
                for key, value in raw.items():
                    key = renames.get(key, key)
                    if key not in valid_columns:
                        dropped_columns.add(key)
                        continue
                    row[key] = _decode_value(table.columns[key], value)
                if not row:
                    stats["skipped_conflict"] += 1
                    continue
                if mode == "merge" and pk_columns and all(c in row for c in pk_columns):
                    existing = db.execute(
                        select(*[table.columns[c] for c in pk_columns]).where(
                            *[table.columns[c] == row[c] for c in pk_columns]
                        )
                    ).first()
                    if existing is not None:
                        stats["skipped_existing"] += 1
                        continue
                if not _try_insert(row):
                    retry_rows.append(row)
                    continue
                stats["inserted"] += 1
                if table.name == "files":
                    restored_file_ids.append(row.get("id"))
            # Second chance: rows that failed on the first pass may have depended on a later row
            # of the SAME table (self-referencing FKs, e.g. works.merged_into_id) — the parent is
            # inserted by now, so retry once before counting the row as lost.
            for row in retry_rows:
                if _try_insert(row):
                    stats["inserted"] += 1
                    if table.name == "files":
                        restored_file_ids.append(row.get("id"))
                else:
                    stats["skipped_conflict"] += 1
            stats["dropped_columns"] = sorted(dropped_columns)
            summary["tables"][table.name] = stats
        db.flush()

        # Owner-lockout guard (replace only): if the backup brought no usable owner, re-insert the
        # pre-restore owner account so the person doing the restore keeps access.
        if mode == "replace" and owner_snapshot is not None:
            users = Base.metadata.tables["users"]
            has_owner = db.execute(select(users.c.id).where(users.c.role == "owner")).first()
            if has_owner is None:
                try:
                    with db.begin_nested():
                        db.execute(users.insert().values(**owner_snapshot))
                    summary["owner_reinserted"] = True
                except Exception:  # noqa: BLE001 - username collision: backup's account wins
                    logger.warning("could not re-insert the pre-restore owner", exc_info=True)

        # PDF pairing: every restored File row must be backed by a real PDF.
        pdf_sources: list = []
        if pdf_root_alias:
            pdf_sources.append(
                ("root", _pdf_map_from_root(_resolve_pdf_root(db, pdf_root_alias, settings)))
            )
        archive_pdfs = {
            m[len("pdfs/") : -len(".pdf")]: m
            for m in members
            if m.startswith("pdfs/") and m.endswith(".pdf")
        }
        summary["files_dropped_sha256"] = []
        if restored_file_ids:
            managed_root = Path(settings.managed_library_root).expanduser().resolve()
            managed_root.mkdir(parents=True, exist_ok=True)
            files_table = Base.metadata.tables["files"]
            rows = db.execute(
                select(files_table.c.id, files_table.c.sha256).where(
                    files_table.c.id.in_([i for i in restored_file_ids if i is not None])
                )
            ).all()
            for file_id, sha in rows:
                dest = content_addressed_path(managed_root, sha)
                found = dest.exists()
                if not found and sha in archive_pdfs:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(archive_pdfs[sha]) as src, dest.open("wb") as out:
                        shutil.copyfileobj(src, out)
                    found = True
                if not found:
                    for _label, mapping in pdf_sources:
                        source = mapping.get(sha)
                        if source is not None:
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(source, dest)
                            found = True
                            break
                if found:
                    summary["files_paired"] += 1
                else:
                    # No PDF anywhere → remove the file record and its links, exactly as if the
                    # user had deleted the file manually. The paper record survives.
                    _drop_file_record(db, file_id)
                    summary["files_dropped"] += 1
                    summary["files_dropped_sha256"].append(sha)
        db.flush()

    record_event(
        db,
        "backup.restored",
        actor_user_id=actor_user_id,
        entity_type="backup",
        entity_id=path.name,
        details={
            "mode": mode,
            "inserted": sum(t["inserted"] for t in summary["tables"].values()),
            "skipped_existing": sum(t["skipped_existing"] for t in summary["tables"].values()),
            "skipped_conflict": sum(t["skipped_conflict"] for t in summary["tables"].values()),
            "files_paired": summary["files_paired"],
            "files_dropped": summary["files_dropped"],
        },
    )
    return summary


def _drop_file_record(db: Session, file_id) -> None:
    """Delete a file row + its dependents (links/locations/segments), keeping the works."""
    tables = Base.metadata.tables
    for name, column in (
        ("file_work_links", "file_id"),
        ("locations", "file_id"),
        ("file_segments", "file_id"),
        ("agent_files", "file_id"),
    ):
        table = tables.get(name)
        if table is not None and column in table.columns:
            db.execute(delete(table).where(table.columns[column] == file_id))
    files = tables["files"]
    db.execute(delete(files).where(files.c.id == file_id))
