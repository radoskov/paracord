"""Authentication API schemas."""

from pydantic import BaseModel, Field


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
