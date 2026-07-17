"""Per-user UI preferences endpoints (GET/PUT).

Any authenticated user (owner/admin/editor/reader) reads and writes their own preferences blob;
preferences are not role-gated. Storage is a YAML file keyed by user id (see services.preferences).
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict

from app.api.deps import require_authenticated_user
from app.models.user import User
from app.services.preferences import (
    PreferencesUnavailable,
    read_preferences,
    write_preferences,
)

router = APIRouter()
AUTH_DEP = Depends(require_authenticated_user)


class LibraryColumnSort(BaseModel):
    key: str
    order: str = "desc"

    # Forward-compatible: tolerate unknown keys so an older server doesn't reject a newer client.
    model_config = ConfigDict(extra="allow")


class LibraryColumnPrefs(BaseModel):
    order: list[str] = []
    visible: list[str] = []
    sort: LibraryColumnSort | None = None
    # Per-column width RATIOS (relative weights; the client divides the list width by their sum)
    # and the row-divider-lines toggle. Validated/clamped client-side (normalizeColumnPrefs).
    widths: dict[str, float] | None = None
    dividers: bool | None = None

    model_config = ConfigDict(extra="allow")


class UserPreferences(BaseModel):
    library_columns: LibraryColumnPrefs | None = None

    # Loose/forward-compatible: keep any other preference sections a future client may add.
    model_config = ConfigDict(extra="allow")


@router.get("", response_model=UserPreferences)
def get_preferences(user: User = AUTH_DEP) -> UserPreferences:
    """Return the caller's stored preferences (empty defaults if none saved yet)."""
    return UserPreferences.model_validate(read_preferences(user.id))


@router.put("", response_model=UserPreferences)
def put_preferences(payload: UserPreferences, user: User = AUTH_DEP) -> UserPreferences:
    """Replace the caller's preferences blob and return what was stored."""
    blob: dict[str, Any] = payload.model_dump(exclude_none=True)
    try:
        stored = write_preferences(user.id, blob)
    except PreferencesUnavailable as exc:
        # Read-only filesystem (or similar): the UI keeps the change in localStorage and tells the
        # user it was "saved locally only".
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Preferences could not be saved on the server (read-only storage); "
            "your change is saved locally only.",
        ) from exc
    return UserPreferences.model_validate(stored)
