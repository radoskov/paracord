"""Durable agent file-state index (SPEC §32.2).

A small SQLite store mapping each indexed file's opaque ``local_file_id`` (content hash) to its
**real on-disk path** (kept local-only — never sent to the server), plus the per-file action,
teleport policy, cached processing state, and the reject-forever block. Restored on restart so
status/monitoring survive a stop/start.
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    local_file_id   TEXT PRIMARY KEY,
    virtual_path    TEXT,
    real_path       TEXT NOT NULL,
    sha256          TEXT NOT NULL,
    size_bytes      INTEGER NOT NULL DEFAULT 0,
    mtime           REAL,
    import_action   TEXT NOT NULL DEFAULT 'index_only',
    teleport_policy TEXT NOT NULL DEFAULT 'ask',
    processing_state TEXT NOT NULL DEFAULT 'indexed',
    teleport_blocked INTEGER NOT NULL DEFAULT 0,
    present         INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_files_real_path ON files (real_path);
"""


@dataclass
class FileRecord:
    local_file_id: str
    virtual_path: str | None
    real_path: str
    sha256: str
    size_bytes: int
    import_action: str
    teleport_policy: str
    processing_state: str
    teleport_blocked: bool
    present: bool


def default_state_path() -> Path:
    import os

    env = os.environ.get("PARACORD_AGENT_HOME")
    base = Path(env).expanduser() if env else Path("~/.local/share/paracord-agent").expanduser()
    return base / "state.sqlite3"


class AgentState:
    """SQLite-backed per-file state for the agent."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path).expanduser() if path else default_state_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        # Migrate a pre-existing DB that lacks the mtime column (added for incremental scans).
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(files)")}
        if "mtime" not in columns:
            self._conn.execute("ALTER TABLE files ADD COLUMN mtime REAL")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def upsert(
        self,
        *,
        local_file_id: str,
        real_path: str,
        sha256: str,
        size_bytes: int,
        virtual_path: str | None = None,
        import_action: str = "index_only",
        teleport_policy: str = "ask",
        mtime: float | None = None,
    ) -> None:
        """Insert/refresh a file, preserving processing_state and block on update."""
        self._conn.execute(
            """
            INSERT INTO files (local_file_id, virtual_path, real_path, sha256, size_bytes, mtime,
                               import_action, teleport_policy, present)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(local_file_id) DO UPDATE SET
                virtual_path=excluded.virtual_path,
                real_path=excluded.real_path,
                sha256=excluded.sha256,
                size_bytes=excluded.size_bytes,
                mtime=excluded.mtime,
                import_action=excluded.import_action,
                teleport_policy=excluded.teleport_policy,
                present=1
            """,
            (
                local_file_id,
                virtual_path,
                real_path,
                sha256,
                size_bytes,
                mtime,
                import_action,
                teleport_policy,
            ),
        )
        self._conn.commit()

    def hash_cache(self) -> dict[str, tuple[int, float | None, str]]:
        """Map ``real_path`` → ``(size_bytes, mtime, local_file_id)`` for incremental scans.

        Lets a rescan skip re-hashing a file whose path/size/mtime are unchanged (the content hash
        is the ``local_file_id``), turning a full re-read of the corpus into a cheap stat per file.
        """
        rows = self._conn.execute(
            "SELECT real_path, size_bytes, mtime, local_file_id FROM files"
        ).fetchall()
        return {r["real_path"]: (r["size_bytes"], r["mtime"], r["local_file_id"]) for r in rows}

    def forget(self, local_file_id: str) -> bool:
        """Drop a file from the local index (the on-disk file is untouched). Returns True if a row went."""
        cur = self._conn.execute("DELETE FROM files WHERE local_file_id=?", (local_file_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def resolve_path(self, local_file_id: str) -> Path | None:
        """Return the real on-disk path for an indexed id (local-only resolution), or None."""
        row = self._conn.execute(
            "SELECT real_path FROM files WHERE local_file_id=?", (local_file_id,)
        ).fetchone()
        return Path(row["real_path"]) if row else None

    def set_processing_state(self, local_file_id: str, state: str) -> None:
        self._conn.execute(
            "UPDATE files SET processing_state=? WHERE local_file_id=?", (state, local_file_id)
        )
        self._conn.commit()

    def set_blocked(self, local_file_id: str, blocked: bool) -> None:
        self._conn.execute(
            "UPDATE files SET teleport_blocked=? WHERE local_file_id=?",
            (1 if blocked else 0, local_file_id),
        )
        self._conn.commit()

    def is_blocked(self, local_file_id: str) -> bool:
        row = self._conn.execute(
            "SELECT teleport_blocked FROM files WHERE local_file_id=?", (local_file_id,)
        ).fetchone()
        return bool(row and row["teleport_blocked"])

    def mark_absent_except(
        self, present_ids: set[str], real_path_prefix: str | None = None
    ) -> list[str]:
        """Mark files not in ``present_ids`` as gone; return the ids newly marked absent.

        Optionally scoped to files under ``real_path_prefix`` (so rescanning one folder doesn't
        flag files from other roots).
        """
        rows = self._conn.execute("SELECT local_file_id, real_path, present FROM files").fetchall()
        newly_absent: list[str] = []
        for row in rows:
            if real_path_prefix and not str(row["real_path"]).startswith(real_path_prefix):
                continue
            if row["local_file_id"] not in present_ids and row["present"]:
                self._conn.execute(
                    "UPDATE files SET present=0 WHERE local_file_id=?", (row["local_file_id"],)
                )
                newly_absent.append(row["local_file_id"])
        self._conn.commit()
        return newly_absent

    def all_files(self) -> list[FileRecord]:
        rows = self._conn.execute("SELECT * FROM files ORDER BY virtual_path").fetchall()
        return [
            FileRecord(
                local_file_id=r["local_file_id"],
                virtual_path=r["virtual_path"],
                real_path=r["real_path"],
                sha256=r["sha256"],
                size_bytes=r["size_bytes"],
                import_action=r["import_action"],
                teleport_policy=r["teleport_policy"],
                processing_state=r["processing_state"],
                teleport_blocked=bool(r["teleport_blocked"]),
                present=bool(r["present"]),
            )
            for r in rows
        ]
