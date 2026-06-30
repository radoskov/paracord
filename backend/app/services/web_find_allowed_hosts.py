"""Owner/admin-managed find-on-web allowed download hosts (batch 2 #5 hardening).

Find-on-web may only download a PDF whose final (post-redirect) host is on the **merged**
allowlist: the built-in :data:`app.services.web_find.DEFAULT_ALLOWED_HOSTS` PLUS the GUI-managed
rows in the ``web_find_allowed_hosts`` table. This module owns the listing + add/remove of the DB
rows.

Safety: the denylist always wins over the allowlist (enforced in
:func:`app.services.web_find.download_and_attach`), so adding a shadow-library host here can never
enable a download. A DB row can never shadow / remove a default entry, and a default entry can
never be removed via the GUI. Host patterns are validated as plausible hostnames (exact host,
parent-domain suffix, or an explicit ``*.`` subdomain wildcard) and deduped across the whole
merged set.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.web_find_allowed_host import WebFindAllowedHost
from app.services.web_find import DEFAULT_ALLOWED_HOSTS

# A plausible hostname label: alphanumerics + hyphen, not starting/ending with a hyphen. The whole
# pattern is one or more dot-separated labels, optionally prefixed with a single "*." wildcard.
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOST_RE = re.compile(rf"^(?:\*\.)?{_LABEL}(?:\.{_LABEL})+$")


def normalize_host(host: str) -> str:
    """Lower-case, strip whitespace and a trailing dot from a host pattern."""
    return (host or "").strip().lower().rstrip(".")


def is_valid_host_pattern(host: str) -> bool:
    """Return True if ``host`` is a plausible allowlist pattern (multi-label, optional ``*.``)."""
    candidate = normalize_host(host)
    if not candidate or len(candidate) > 255:
        return False
    return bool(_HOST_RE.match(candidate))


def merged_allowed_hosts(db: Session) -> set[str]:
    """Return the merged allowed-host set: built-in defaults ∪ GUI-managed DB rows."""
    db_hosts = {normalize_host(h) for h in db.scalars(select(WebFindAllowedHost.host)).all()}
    return set(DEFAULT_ALLOWED_HOSTS) | {h for h in db_hosts if h}


def list_merged_hosts(db: Session) -> list[dict]:
    """List the merged allowed hosts, marking each as default-fixed or DB-removable.

    Returns one dict per host: ``{host, source ("default"|"db"), removable, id}``. ``id`` is the DB
    row id for removable entries (``None`` for defaults). Defaults sort first, then DB rows; a DB
    row duplicating a default is hidden (the default wins and is non-removable).
    """
    defaults = {normalize_host(h) for h in DEFAULT_ALLOWED_HOSTS}
    db_rows = {
        normalize_host(row.host): row for row in db.scalars(select(WebFindAllowedHost)).all()
    }

    items: list[dict] = []
    for host in sorted(defaults):
        items.append({"host": host, "source": "default", "removable": False, "id": None})
    for host, row in sorted(db_rows.items()):
        if host in defaults:
            # A default shadows the DB row (default wins, non-removable); don't list the duplicate.
            continue
        items.append({"host": host, "source": "db", "removable": True, "id": row.id})
    return items


def add_allowed_host(
    db: Session,
    *,
    host: str,
    created_by_user_id: uuid.UUID,
) -> WebFindAllowedHost:
    """Add a GUI-managed allowed host after validating it as a plausible hostname pattern.

    Raises ``ValueError`` if the host is blank, not a plausible hostname, or already present in the
    merged set (a default or an existing DB row).
    """
    candidate = normalize_host(host)
    if not candidate:
        raise ValueError("Host is required")
    if not is_valid_host_pattern(candidate):
        raise ValueError("Host must be a plausible hostname (e.g. example.org or *.example.org)")
    # Dedupe across the whole MERGED set so a GUI host can never duplicate a default or DB row.
    if candidate in merged_allowed_hosts(db):
        raise ValueError("Host already in the allowed-downloads list")

    row = WebFindAllowedHost(host=candidate, created_by_user_id=created_by_user_id)
    db.add(row)
    db.flush()
    return row


def remove_allowed_host(db: Session, *, host_id: uuid.UUID) -> None:
    """Remove a GUI-managed allowed host by id.

    Raises ``ValueError`` if no such DB row exists (default hosts have no DB row, so they can never
    be targeted here).
    """
    row = db.get(WebFindAllowedHost, host_id)
    if row is None:
        raise ValueError("Allowed host not found")
    db.delete(row)
    db.flush()
