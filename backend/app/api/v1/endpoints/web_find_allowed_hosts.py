"""Owner/admin-managed find-on-web allowed download hosts (batch 2 #5 hardening).

GUI backing for the find-on-web download-host allowlist. Lists the MERGED set (built-in defaults +
GUI-managed DB rows), and lets the owner OR an admin add/remove the DB rows at runtime — the
defaults are never written to and can never be removed here. Managing the allowlist is admin-or-owner
(unlike the owner-only import roots, per the maintainer's decision for this feature).
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_admin, require_owner
from app.db.session import get_db
from app.models.user import User
from app.services.audit import record_event
from app.services.web_find_allowed_hosts import (
    add_allowed_host,
    list_merged_hosts,
    remove_allowed_host,
)
from app.services.web_find_settings import (
    DOWNLOAD_POLICIES,
    get_download_policy,
    set_download_policy,
)

router = APIRouter()
DB_DEP = Depends(get_db)
ADMIN_DEP = Depends(require_admin)
OWNER_DEP = Depends(require_owner)


class WebFindDownloadPolicyOut(BaseModel):
    policy: str
    allowed: list[str]


class WebFindDownloadPolicyUpdate(BaseModel):
    policy: str


@router.get("/web-find/download-policy", response_model=WebFindDownloadPolicyOut)
def get_web_find_download_policy(db: Session = DB_DEP, _owner: User = OWNER_DEP) -> dict:
    """Return the global find-on-web download-policy mode + allowed values. Owner-only."""
    return {"policy": get_download_policy(db), "allowed": list(DOWNLOAD_POLICIES)}


@router.put("/web-find/download-policy", response_model=WebFindDownloadPolicyOut)
def set_web_find_download_policy(
    payload: WebFindDownloadPolicyUpdate, db: Session = DB_DEP, owner: User = OWNER_DEP
) -> dict:
    """Set the global find-on-web download-policy mode (restricted/careful/unrestricted). Owner-only."""
    try:
        policy = set_download_policy(db, policy=payload.policy, actor_user_id=owner.id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    record_event(
        db,
        "web_find.download_policy_changed",
        actor_user_id=owner.id,
        entity_type="web_find_settings",
        details={"policy": policy},
    )
    db.commit()
    return {"policy": policy, "allowed": list(DOWNLOAD_POLICIES)}


class WebFindAllowedHostCreate(BaseModel):
    host: str


class WebFindAllowedHostOut(BaseModel):
    host: str
    source: str  # "default" (fixed) | "db" (removable)
    removable: bool
    id: uuid.UUID | None = None


@router.get("/web-find/allowed-hosts", response_model=list[WebFindAllowedHostOut])
def list_web_find_allowed_hosts(db: Session = DB_DEP, _admin: User = ADMIN_DEP) -> list[dict]:
    """List the merged find-on-web allowed download hosts (default-fixed + DB-removable)."""
    return list_merged_hosts(db)


@router.post(
    "/web-find/allowed-hosts",
    response_model=WebFindAllowedHostOut,
    status_code=status.HTTP_201_CREATED,
)
def create_web_find_allowed_host(
    payload: WebFindAllowedHostCreate, db: Session = DB_DEP, admin: User = ADMIN_DEP
) -> dict:
    """Add a GUI-managed allowed download host (must be a plausible hostname; unique). Admin/owner."""
    try:
        row = add_allowed_host(db, host=payload.host, created_by_user_id=admin.id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    record_event(
        db,
        "web_find.allowed_host_added",
        actor_user_id=admin.id,
        entity_type="web_find_allowed_host",
        entity_id=str(row.id),
        details={"host": row.host},
    )
    db.commit()
    db.refresh(row)
    return {"host": row.host, "source": "db", "removable": True, "id": row.id}


@router.delete("/web-find/allowed-hosts/{host_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_web_find_allowed_host(
    host_id: uuid.UUID, db: Session = DB_DEP, admin: User = ADMIN_DEP
) -> None:
    """Remove a GUI-managed allowed download host. Admin/owner; defaults have no DB row to remove."""
    try:
        remove_allowed_host(db, host_id=host_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    record_event(
        db,
        "web_find.allowed_host_removed",
        actor_user_id=admin.id,
        entity_type="web_find_allowed_host",
        entity_id=str(host_id),
    )
    db.commit()
