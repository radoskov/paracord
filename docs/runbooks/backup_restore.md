# Backup & restore (SPEC §8.16)

PaRacORD's durable state is two things: the **PostgreSQL database** (works, metadata, organization,
annotations, audit log, agent records) and the **managed library volume** (`paperracks_library`,
holding teleported/uploaded PDFs). Backing up both gives a complete, restorable snapshot.

## Back up

```bash
make backup                 # writes to ./backups
make backup BACKUP_DIR=/mnt/nas/paracord-backups
```

This produces two timestamped artifacts:

- `db-<timestamp>.sql.gz` — a gzipped `pg_dump` of the whole database.
- `library-<timestamp>.tar.gz` — the managed library (`/app/storage`) as a tarball.

The database dump is portable across PostgreSQL minor versions. Run it on a schedule (cron /
systemd timer) and copy the artifacts off-host; the corpus itself relies on the host's disk/volume
encryption for at-rest confidentiality (see `SECURITY.md`).

## Restore

Bring the stack up (so PostgreSQL is running), then load a dump:

```bash
make up-infra                                     # or `make up`
make restore RESTORE=backups/db-20260630-120000.sql.gz
```

### Dry run first

Before applying a restore (which drops and replaces existing data), validate the dump and see what
it *would* do without writing anything:

```bash
make restore-dry-run RESTORE=backups/db-20260630-120000.sql.gz
```

This checks gzip integrity, confirms the file is a recognizable `pg_dump`, reports the target
database name/user, and prints how many `CREATE TABLE` / `COPY` / `INSERT INTO` statements the dump
contains — all **without** touching the database. Nothing is applied until you re-run `make restore`.

`restore` pipes the dump into `psql`; objects are recreated and existing rows replaced. For the
managed library, extract the tarball back into the volume:

```bash
docker compose run --rm --no-deps -v "$(pwd)/backups:/backup" api \
  sh -c 'tar xzf /backup/library-20260630-120000.tar.gz -C /app'
```

After restoring, run `make migrate` to ensure the schema is at the current Alembic head, then
`make prod-smoke` (or `make up` + open the app) to verify the API is healthy.

## Verify a backup is good

`make prod-smoke` builds and starts the production stack and asserts `/api/v1/health` responds —
run it after a restore drill to confirm the snapshot is loadable end-to-end.
