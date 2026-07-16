"""Authentication endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.deps import require_authenticated_user
from app.core.config import get_settings
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    ProfileUpdateRequest,
    TokenResponse,
)
from app.services import login_throttle
from app.services import users as user_service
from app.services.audit import record_event
from app.services.auth import (
    authenticate_user,
    change_password,
    create_user_session,
    hash_token,
    revoke_all_user_sessions,
    revoke_token,
)

router = APIRouter()


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> TokenResponse:
    """Authenticate a user and create a revocable bearer-token session.

    Repeated failures for a username are throttled (SPEC §7.2): after the configured number of
    failures within the window the account is temporarily locked with a ``Retry-After`` hint.
    """
    settings = get_settings()
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    throttle_key = payload.username.strip().lower()

    locked, retry_after = login_throttle.lock_state(
        throttle_key,
        max_failures=settings.login_max_failures,
        window_minutes=settings.login_lockout_minutes,
    )
    if locked:
        record_event(
            db,
            "auth.login_locked",
            ip_address=client_ip,
            user_agent=user_agent,
            details={"username": payload.username},
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )

    user = authenticate_user(db, payload.username, payload.password)
    if user is None:
        login_throttle.record_failure(throttle_key, window_minutes=settings.login_lockout_minutes)
        record_event(
            db,
            "auth.login_failure",
            ip_address=client_ip,
            user_agent=user_agent,
            details={"username": payload.username},
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    login_throttle.clear(throttle_key)
    from datetime import UTC, datetime

    user.last_login_at = datetime.now(UTC)
    token, _session = create_user_session(db, user, ttl_minutes=settings.session_ttl_minutes)
    record_event(
        db,
        "auth.login_success",
        actor_user_id=user.id,
        ip_address=client_ip,
        user_agent=user_agent,
    )
    db.commit()
    return TokenResponse(access_token=token)


def _profile_payload(user: User) -> dict:
    """Build the caller-facing profile dict shared by ``/me`` (read) and profile-update (write)."""
    return {
        "id": str(user.id),
        "username": user.username,
        "role": user.role,
        "display_name": user.display_name,
        "email": user.email,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "papers_per_page": user.papers_per_page,
        "theme": user.theme,
    }


@router.get("/me")
def whoami(user: User = Depends(require_authenticated_user)) -> dict:
    """Return the authenticated caller's identity and profile (SPEC §9.3)."""
    return _profile_payload(user)


@router.patch("/me")
def update_me(
    payload: ProfileUpdateRequest,
    user: User = Depends(require_authenticated_user),
    db: Session = Depends(get_db),
) -> dict:
    """Update the caller's own editable profile (display name, email, papers per page)."""
    try:
        user_service.update_profile(
            db,
            user=user,
            changes=payload.model_dump(exclude_unset=True),
            actor_user_id=user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    db.refresh(user)
    return _profile_payload(user)


@router.post("/change-password")
def change_own_password(
    payload: ChangePasswordRequest,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    user: User = Depends(require_authenticated_user),
    db: Session = Depends(get_db),
) -> dict[str, str | int]:
    """Change the caller's password and revoke their other sessions (the current one is kept)."""
    try:
        change_password(
            db,
            user,
            current_password=payload.current_password,
            new_password=payload.new_password,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    _, _, raw_token = (authorization or "").partition(" ")
    kept = hash_token(raw_token.strip()) if raw_token else None
    revoked = revoke_all_user_sessions(db, user.id, except_token_hash=kept)
    record_event(
        db,
        "auth.password_changed",
        actor_user_id=user.id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        details={"sessions_revoked": revoked},
    )
    db.commit()
    return {"status": "ok", "sessions_revoked": revoked}


@router.post("/logout")
def logout(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """Invalidate the current session/token."""
    scheme, _, raw_token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    session = revoke_token(db, raw_token.strip())
    if session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bearer token")

    record_event(
        db,
        "auth.logout",
        actor_user_id=session.user_id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()
    return {"status": "ok"}
