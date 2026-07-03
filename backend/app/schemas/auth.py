"""Authentication API schemas."""

from pydantic import BaseModel, Field, field_validator

from app.core.themes import KNOWN_THEME_IDS


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
    # Preferred GUI theme id (P3); None resets to the boot default. Validated against the bundled
    # theme ids so an unknown id is rejected (422) rather than persisted.
    theme: str | None = None

    @field_validator("theme")
    @classmethod
    def _validate_theme(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if cleaned == "":
            return None
        if cleaned not in KNOWN_THEME_IDS:
            raise ValueError(f"Unknown theme id: {value!r}")
        return cleaned
