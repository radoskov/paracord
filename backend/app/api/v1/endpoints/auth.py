"""Authentication endpoints."""

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.schemas.auth import LoginRequest, TokenResponse
from app.services.audit import record_event
from app.services.auth import authenticate_user, create_user_session, revoke_token

router = APIRouter()


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> TokenResponse:
    """Authenticate a user and create a revocable bearer-token session."""
    user = authenticate_user(db, payload.username, payload.password)
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    if user is None:
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

    token, _session = create_user_session(
        db,
        user,
        ttl_minutes=get_settings().session_ttl_minutes,
    )
    record_event(
        db,
        "auth.login_success",
        actor_user_id=user.id,
        ip_address=client_ip,
        user_agent=user_agent,
    )
    db.commit()
    return TokenResponse(access_token=token)


@router.post("/logout")
def logout(
    request: Request,
    authorization: str | None = Header(default=None),
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
