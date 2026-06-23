"""Authentication endpoints."""

from fastapi import APIRouter, HTTPException, status

from app.schemas.auth import LoginRequest, TokenResponse

router = APIRouter()


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest) -> TokenResponse:
    """Authenticate a user.

    TODO: Implement password verification, session/token creation, audit logging, and lockout.
    """
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="Login not implemented")


@router.post("/logout")
def logout() -> dict[str, str]:
    """Invalidate the current session/token."""
    return {"status": "todo"}
