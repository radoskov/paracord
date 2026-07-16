"""Database engine and session helpers."""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()
# pool_pre_ping: recycles connections that have gone stale (e.g. DB restart, idle timeout) instead
# of raising on first use — cheap ping before checkout, prevents "server closed the connection"
# errors on long-lived processes (API workers, background job workers).
engine = create_engine(settings.database_url, pool_pre_ping=True)
# autocommit/autoflush disabled: callers control commit/flush explicitly (e.g. via ``get_db`` below,
# or ``with SessionLocal() as db: ... db.commit()`` in scripts/workers) rather than SQLAlchemy
# flushing implicitly before every query.
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a SQLAlchemy session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
