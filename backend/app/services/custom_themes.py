"""Runtime custom-theme store (Theming P4).

Thin persistence layer over :class:`CustomTheme`: validate + upsert by slug, list, resolve and
delete. Validation/resolution lives in ``app.core.theme_schema``; readability warnings ride back to
the caller so the admin UI can show them. Audit events are recorded by the endpoint (mirrors the
web-find allowed-hosts pattern).
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.theme_schema import ResolvedTheme, validate_and_resolve
from app.models.custom_theme import CustomTheme


def create_or_replace_theme(
    db: Session, *, yaml_text: str, actor_user_id: uuid.UUID
) -> tuple[CustomTheme, ResolvedTheme]:
    """Validate a YAML theme and upsert it by slug.

    Returns the persisted row and the :class:`ResolvedTheme` (whose ``warnings`` the caller surfaces).
    Raises :class:`app.core.theme_schema.ThemeValidationError` (→ 400) on malformed input.
    """
    resolved = validate_and_resolve(yaml_text)
    row = db.scalar(select(CustomTheme).where(CustomTheme.slug == resolved.slug))
    if row is None:
        row = CustomTheme(slug=resolved.slug, created_by=actor_user_id)
        db.add(row)
    row.name = resolved.name
    row.mode = resolved.mode
    row.temperature = resolved.temperature
    row.yaml_source = yaml_text
    db.flush()
    return row, resolved


def list_themes(db: Session) -> list[CustomTheme]:
    """All custom themes, oldest first (stable order in the picker)."""
    return list(db.scalars(select(CustomTheme).order_by(CustomTheme.created_at)))


def get_theme(db: Session, slug: str) -> CustomTheme | None:
    """The custom theme row for ``slug`` (or None)."""
    return db.scalar(select(CustomTheme).where(CustomTheme.slug == slug))


def delete_theme(db: Session, slug: str) -> bool:
    """Delete the custom theme named ``slug``; returns whether a row was removed."""
    row = db.scalar(select(CustomTheme).where(CustomTheme.slug == slug))
    if row is None:
        return False
    db.delete(row)
    db.flush()
    return True


def custom_theme_slugs(db: Session) -> set[str]:
    """The set of custom theme slugs (used to validate a per-user theme preference)."""
    return set(db.scalars(select(CustomTheme.slug)))


def resolve_row(row: CustomTheme) -> ResolvedTheme:
    """Re-derive the resolved theme object from a row's stored YAML."""
    return validate_and_resolve(row.yaml_source)


def list_item(row: CustomTheme) -> dict[str, Any]:
    """Picker-list projection: id/name/mode/temperature + a swatch (re-resolved from YAML)."""
    resolved = resolve_row(row)
    return {
        "id": row.slug,
        "name": row.name,
        "mode": row.mode,
        "temperature": row.temperature,
        "swatch": resolved.swatch(),
    }
