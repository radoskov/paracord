"""Authentication API schemas."""

import re

from pydantic import BaseModel, Field, field_validator

_THEME_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class ProfileUpdateRequest(BaseModel):
    """Self-service profile edits. Username and role are not editable here."""

    display_name: str | None = None
    email: str | None = None
    # Preferred Library page size (D18); None resets to the server default. Only enforced when the
    # key is present in the request (partial update).
    papers_per_page: int | None = Field(default=None, ge=1)
    # Preferred GUI theme id (P3; a bundled OR a custom-theme slug since P4). None resets to the
    # boot default. Only the slug *format* is checked here (a malformed value is rejected 422);
    # membership against the bundled + custom theme ids is enforced DB-side in the service layer
    # (an unknown id → 400) since custom themes live in the database.
    theme: str | None = None

    @field_validator("theme")
    @classmethod
    def _validate_theme(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if cleaned == "":
            return None
        if not _THEME_SLUG_RE.match(cleaned):
            raise ValueError(f"Malformed theme id: {value!r}")
        return cleaned
