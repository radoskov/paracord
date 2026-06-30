"""Owner-managed server import roots (batch 2 #19).

Owner-only GUI backing for the "Server folder" import whitelist. Lists the MERGED set (read-only
``server.yaml`` entries + GUI-managed DB rows), and lets the owner add/remove the DB rows at runtime
— the yaml entries are never written to and can never be removed here. Managing roots is owner-only
(admin is NOT sufficient, per the batch 2 #19/#20 decision).
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_owner
from app.core.config import get_settings
from app.db.session import get_db
from app.models.user import User
from app.services.audit import record_event
from app.services.import_roots import add_import_root, list_merged_roots, remove_import_root

router = APIRouter()
DB_DEP = Depends(get_db)
OWNER_DEP = Depends(require_owner)


class ImportRootCreate(BaseModel):
    alias: str
    path: str


class ImportRootOut(BaseModel):
    alias: str
    path: str
    source: str  # "yaml" (fixed) | "db" (removable)
    removable: bool
    id: uuid.UUID | None = None
    exists: bool


@router.get("/import-roots", response_model=list[ImportRootOut])
def list_import_roots(db: Session = DB_DEP, _owner: User = OWNER_DEP) -> list[dict]:
    """List the merged allowed import roots (yaml-fixed + DB-removable). Owner only."""
    return list_merged_roots(db, get_settings())


@router.post("/import-roots", response_model=ImportRootOut, status_code=status.HTTP_201_CREATED)
def create_import_root(
    payload: ImportRootCreate, db: Session = DB_DEP, owner: User = OWNER_DEP
) -> dict:
    """Add a GUI-managed import root (path must exist + be a dir; alias unique). Owner only."""
    try:
        root = add_import_root(
            db,
            settings=get_settings(),
            alias=payload.alias,
            path=payload.path,
            created_by_user_id=owner.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    record_event(
        db,
        "import_root.added",
        actor_user_id=owner.id,
        entity_type="import_root",
        entity_id=str(root.id),
        details={"alias": root.alias, "path": root.path},
    )
    db.commit()
    db.refresh(root)
    return {
        "alias": root.alias,
        "path": root.path,
        "source": "db",
        "removable": True,
        "id": root.id,
        "exists": True,
    }


@router.delete("/import-roots/{root_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_import_root(root_id: uuid.UUID, db: Session = DB_DEP, owner: User = OWNER_DEP) -> None:
    """Remove a GUI-managed import root. Owner only; yaml-fixed entries have no DB row to remove."""
    try:
        remove_import_root(db, root_id=root_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    record_event(
        db,
        "import_root.removed",
        actor_user_id=owner.id,
        entity_type="import_root",
        entity_id=str(root_id),
    )
    db.commit()
