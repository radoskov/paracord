"""SQLAlchemy declarative base."""

# Importing pgvector's SQLAlchemy integration registers the `vector` column type into the Postgres
# dialect's ischema_names as a side-effect. That makes schema *reflection* (Alembic autogenerate and
# the migration-parity test's `inspect(...).get_columns()` / `compare_metadata()`) recognize the
# pgvector columns (embeddings.vector_pg, work_chunks.vec_*) that are provisioned via raw DDL and
# kept off the ORM — instead of emitting "Did not recognize type 'vector'". This is reflection-only:
# the app reads/writes those columns through raw SQL and parses the text form itself, and we do NOT
# register the psycopg adapter, so that runtime path is entirely unaffected. Imported here (the
# module every model + Alembic's env + the parity test load) so the registration is always in place.
import pgvector.sqlalchemy  # noqa: F401
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all ORM models."""
