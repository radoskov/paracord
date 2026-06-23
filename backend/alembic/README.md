# Alembic migrations

Run migrations from the repository root:

```bash
alembic -c backend/alembic.ini upgrade head
```

or:

```bash
make migrate
```

The first migration creates the security tables needed by the server-console owner bootstrap and
password reset scripts: `users` and `audit_events`.
