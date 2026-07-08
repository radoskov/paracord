"""Owner-managed global find-on-web settings (find-on-web v2 download-policy modes).

Stores the global download-policy mode in the single-row ``web_find_settings`` table. The mode
governs how strictly ``download_and_attach`` gates the (public, non-denied) download host:

  * ``restricted``    — allow only the merged allow-list (defaults ∪ DB rows).
  * ``careful``       — allow the merged allow-list ∪ the built-in KNOWN_PUBLISHER_HOSTS.
  * ``unrestricted``  — allow allow-list/known hosts with no confirmation; any other public host
                        requires an explicit per-item ``confirmed=true`` (needs-confirmation
                        handshake).

The shadow-library denylist and the private/internal-IP guard are ALWAYS enforced regardless of
mode (they win over everything). An absent row reproduces the conservative ``restricted`` default.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models.web_find_settings import (
    DEFAULT_DOWNLOAD_POLICY,
    WEB_FIND_SETTINGS_SINGLETON_ID,
    WebFindSettings,
)
from app.utils.table_presence import table_present

# The allowed download-policy modes (the conservative default is the first / DEFAULT_DOWNLOAD_POLICY).
DOWNLOAD_POLICIES = ("restricted", "careful", "unrestricted")


def _table_present(db: Session) -> bool:
    """Whether the ``web_find_settings`` table exists (narrow unit-test schemas omit it); a read
    helper must never provoke + roll back an error inside the caller's transaction."""
    return table_present(db, WebFindSettings.__tablename__)


def get_download_policy(db: Session) -> str:
    """Return the effective global download-policy mode (DB row, else the default)."""
    if not _table_present(db):
        return DEFAULT_DOWNLOAD_POLICY
    row = db.get(WebFindSettings, WEB_FIND_SETTINGS_SINGLETON_ID)
    if row is None or not row.download_policy:
        return DEFAULT_DOWNLOAD_POLICY
    return row.download_policy


def set_download_policy(db: Session, *, policy: str, actor_user_id: uuid.UUID | None = None) -> str:
    """Validate + persist the global download-policy mode. Returns the stored mode.

    Raises ``ValueError`` for an unknown mode. The caller commits.
    """
    normalized = (policy or "").strip().lower()
    if normalized not in DOWNLOAD_POLICIES:
        raise ValueError(f"Unknown download policy (allowed: {DOWNLOAD_POLICIES})")
    row = db.get(WebFindSettings, WEB_FIND_SETTINGS_SINGLETON_ID)
    if row is None:
        row = WebFindSettings(id=WEB_FIND_SETTINGS_SINGLETON_ID)
        db.add(row)
    row.download_policy = normalized
    row.updated_by_user_id = actor_user_id
    db.flush()
    return normalized
