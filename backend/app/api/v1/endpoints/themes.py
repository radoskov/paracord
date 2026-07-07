"""Runtime custom GUI themes (Theming P4).

Two surfaces:

* **Read** (any authenticated user, mounted at ``/themes``): ``GET /themes`` lists the custom themes
  for the picker, ``GET /themes/{slug}`` returns one resolved theme object for the frontend to apply.
* **Write** (owner/admin, mounted at ``/admin/themes``): ``POST`` uploads/replaces a theme from YAML
  text (validate-on-load: reject malformed YAML / missing required roles with 400; WARN, don't
  reject, on a categorical palette that fails the readability check), ``DELETE`` removes one.

Create/delete are audit-evented. Bundled themes are compiled into the frontend and are not served
here; the picker merges these custom themes alongside them.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_admin, require_authenticated_user
from app.core.theme_schema import ThemeValidationError
from app.db.session import get_db
from app.models.user import User
from app.services import custom_themes
from app.services.audit import record_event

router = APIRouter()
admin_router = APIRouter()
DB_DEP = Depends(get_db)
AUTH_DEP = Depends(require_authenticated_user)
ADMIN_DEP = Depends(require_admin)


class ThemeSwatch(BaseModel):
    surface: str
    primary: str
    accents: list[str]


class ThemeListItem(BaseModel):
    id: str
    name: str
    mode: str
    temperature: str
    swatch: ThemeSwatch


class ResolvedThemeOut(BaseModel):
    id: str
    name: str
    mode: str
    temperature: str
    tokens: dict[str, dict[str, str]]
    graph: dict[str, Any]


class ThemeSourceOut(BaseModel):
    # The verbatim YAML source of a custom theme, so the admin editor can load it as a template.
    id: str
    yaml: str


class ThemeUpload(BaseModel):
    # The full theme YAML text (same schema as a bundled frontend/themes/*.yaml file).
    yaml: str


class ThemeUploadResult(BaseModel):
    id: str
    name: str
    mode: str
    temperature: str
    # Advisory readability warnings (e.g. a categorical palette that fails the CVD check). The theme
    # is accepted and stored even when non-empty.
    warnings: list[str]


@router.get("", response_model=list[ThemeListItem])
def list_custom_themes(db: Session = DB_DEP, _user: User = AUTH_DEP) -> list[dict]:
    """List the custom themes (id/name/mode/temperature + swatch) to merge into the picker."""
    return [custom_themes.list_item(row) for row in custom_themes.list_themes(db)]


@router.get("/{slug}", response_model=ResolvedThemeOut)
def get_custom_theme(slug: str, db: Session = DB_DEP, _user: User = AUTH_DEP) -> dict:
    """Return one custom theme resolved to the frontend Theme object (tokens + graph)."""
    row = custom_themes.get_theme(db, slug)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Theme not found")
    return custom_themes.resolve_row(row).as_theme_object()


@router.get("/{slug}/source", response_model=ThemeSourceOut)
def get_custom_theme_source(slug: str, db: Session = DB_DEP, _user: User = AUTH_DEP) -> dict:
    """Return a custom theme's verbatim YAML source, for loading it as an editor template."""
    row = custom_themes.get_theme(db, slug)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Theme not found")
    return {"id": row.slug, "yaml": row.yaml_source}


@admin_router.post("/themes", response_model=ThemeUploadResult, status_code=status.HTTP_201_CREATED)
def upload_custom_theme(
    payload: ThemeUpload, db: Session = DB_DEP, admin: User = ADMIN_DEP
) -> dict:
    """Upload or replace a custom theme from YAML text (owner/admin). See module docstring."""
    try:
        row, resolved = custom_themes.create_or_replace_theme(
            db, yaml_text=payload.yaml, actor_user_id=admin.id
        )
    except ThemeValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    record_event(
        db,
        "theme.uploaded",
        actor_user_id=admin.id,
        entity_type="custom_theme",
        entity_id=row.slug,
        details={"name": row.name, "mode": row.mode, "warnings": len(resolved.warnings)},
    )
    db.commit()
    return {
        "id": row.slug,
        "name": row.name,
        "mode": row.mode,
        "temperature": row.temperature,
        "warnings": resolved.warnings,
    }


@admin_router.delete("/themes/{slug}", status_code=status.HTTP_204_NO_CONTENT)
def delete_custom_theme(slug: str, db: Session = DB_DEP, admin: User = ADMIN_DEP) -> None:
    """Delete a custom theme (owner/admin)."""
    if not custom_themes.delete_theme(db, slug):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Theme not found")
    record_event(
        db,
        "theme.deleted",
        actor_user_id=admin.id,
        entity_type="custom_theme",
        entity_id=slug,
    )
    db.commit()
