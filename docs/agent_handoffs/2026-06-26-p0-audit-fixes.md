# Handoff: P0 Audit Fixes + P1 Schema Identifiers (2026-06-26)

## Task name
P0 audit fixes (C3/C4/H1/H4) + P1 schema identifiers (arxiv_base_id, resolution_status)

## Files changed

**Model changes (C3 — FK declarations):**
- `backend/app/models/file.py` — FKs on Location.file_id/source_id, FileSegment.file_id, FileWorkLink.*
- `backend/app/models/work.py` — FK on WorkVersion.work_id; new `arxiv_base_id` column
- `backend/app/models/organization.py` — FKs on ShelfWork/RackShelf/TagLink PKs
- `backend/app/models/source.py` — FK on ImportBatch.source_id
- `backend/app/models/audit.py` — AuditEvent.details uses JSONB variant (C4)
- `backend/app/models/citation.py` — Reference.resolution_status column

**Migration:**
- `backend/alembic/versions/0011_arxiv_base_id_resolution_status.py` — adds arxiv_base_id (backfilled),
  partial unique indexes on doi/arxiv_base_id, adds resolution_status to references

**New service:**
- `backend/app/services/identifiers.py` — shared `arxiv_base_id()` normalizer

**Service updates:**
- `backend/app/services/storage.py` — populates arxiv_base_id at file import
- `backend/app/services/bibtex.py` — populates arxiv_base_id at BibTeX import
- `backend/app/services/citation_graph.py` — persists resolution_status on Reference objects
- `backend/app/services/duplicate_detection.py` — SQL pushdown for arxiv_base_id dedup

**Security / endpoint fixes (H4):**
- `backend/app/api/deps.py` — new `require_agent_token` dependency
- `backend/app/api/v1/endpoints/agents.py` — manifest/teleport gated + return 501; register stub returns 410
- `backend/app/api/v1/endpoints/citations.py` — dead `/contexts` stub removed

**Dependency fix (H1):**
- `backend/requirements.txt` — `httpx2==2.4.0` (was unpinned)
- `agent/requirements.txt` — `httpx2==2.4.0` (was unpinned)

**Docs:**
- `CHANGELOG.md`, `PROGRESS.md` — updated to reflect all changes

## Assumptions made

- `httpx2` is the Pydantic-maintained security-patch fork; pinning it (not reverting to mainline httpx)
  is the correct fix. AUDIT.md's suggestion to revert to mainline httpx is wrong.
- Partial unique indexes (`WHERE col IS NOT NULL`) on `doi` and `arxiv_base_id` are correct for nullable
  identifier columns — Postgres allows multiple NULL values in a UNIQUE column anyway, but partial indexes
  make the intent explicit.
- The backfill in migration 0011 is Postgres-only (a guard on `dialect.name == "postgresql"`) since SQLite
  tests build from `Base.metadata.create_all` and never run migrations.
- ForeignKey additions to existing models do NOT require a new migration since the constraints already
  exist in the database (added by the original migrations). Alembic autogenerate will now see them as matching.

## Tests added or skipped

- All 148 existing tests pass unchanged.
- No new tests added for the FK/JSONB model changes (they are structural; the existing test suite covers
  the affected service paths end-to-end).
- The `test_migration_parity.py` Postgres test should now be extended to assert autogenerate-clean
  (as C2 follow-up) once the JSONB/FK drift is resolved. That extension is not yet done.

## Security implications

- Agent manifest/teleport endpoints previously allowed unauthenticated 500 (NotImplementedError) and
  unauthenticated 200 success responses. Both are now gated behind `require_agent_token` (returning 401
  without a valid agent token) and return 501 when authenticated.
- `httpx2==2.4.0` pins the egress library to a known-good version with security patches from Pydantic team.

## Next recommended tasks

1. **P0/H5** — Production Dockerfile and compose profile (multi-stage, gunicorn, no --reload).
2. **P0/H6** — Regenerate local `.env` from `.env.example` if using `PAPERRACKS_*` prefix (operator fix).
3. **P2/item6** — Navigation shell + Admin UI (users/agents/audit currently raw-HTTP only).
4. **P2/item8** — Metadata review/edit UI + per-field `user_confirmed` locking.
5. **P2/item9** — Shelf/rack citation-context summaries (stub at `POST /ai/summaries`).
6. **P1/item5** — Full H3 SQL pushdown: DOI dedup and BibTeX dedup are still O(n) Python loops.
