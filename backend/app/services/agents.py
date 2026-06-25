"""Owner-gated local agent enrollment (SPEC §11.2).

Flow: an owner mints a short-lived enrollment token; an agent presents it to request enrollment
(creating a ``pending`` agent and consuming the token); an owner approves the agent, which mints
its scoped access token (returned once, stored hashed). Every step writes an audit event.
"""

import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent import Agent, AgentEnrollmentToken
from app.models.user import User
from app.services.audit import record_event
from app.services.auth import hash_token

ENROLL_TOKEN_TTL_MINUTES = 60


def _as_utc(value: datetime) -> datetime:
    """Treat a stored (possibly naive) timestamp as UTC for comparison."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def mint_enrollment_token(
    db: Session, *, owner: User, ttl_minutes: int = ENROLL_TOKEN_TTL_MINUTES
) -> tuple[str, AgentEnrollmentToken]:
    """Create a single-use enrollment token; the raw value is returned only here."""
    raw_token = secrets.token_urlsafe(32)
    token = AgentEnrollmentToken(
        token_hash=hash_token(raw_token),
        created_by_user_id=owner.id,
        expires_at=datetime.now(UTC) + timedelta(minutes=ttl_minutes),
    )
    db.add(token)
    db.flush()
    record_event(
        db,
        "agent.enroll_token_issued",
        actor_user_id=owner.id,
        entity_type="agent_enrollment_token",
        entity_id=str(token.id),
    )
    return raw_token, token


def enroll_agent(db: Session, *, token: str, name: str) -> Agent:
    """Consume a valid enrollment token and create a pending agent."""
    name = (name or "").strip()
    if not name:
        raise ValueError("Agent name is required")
    record = db.scalar(
        select(AgentEnrollmentToken).where(AgentEnrollmentToken.token_hash == hash_token(token))
    )
    if record is None:
        raise ValueError("Invalid enrollment token")
    if record.used_by_agent_id is not None:
        raise ValueError("Enrollment token has already been used")
    if _as_utc(record.expires_at) <= datetime.now(UTC):
        raise ValueError("Enrollment token has expired")

    agent = Agent(name=name, status="pending")
    db.add(agent)
    db.flush()
    record.used_by_agent_id = agent.id
    record.used_at = datetime.now(UTC)
    record_event(
        db,
        "agent.enroll_requested",
        actor_agent_id=agent.id,
        entity_type="agent",
        entity_id=str(agent.id),
        details={"name": name},
    )
    return agent


def approve_agent(db: Session, *, agent_id: uuid.UUID, owner: User) -> tuple[str, Agent]:
    """Approve a pending agent and mint its scoped access token (returned once)."""
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise LookupError("Agent not found")
    if agent.status != "pending":
        raise ValueError(f"Agent is not pending (status={agent.status})")

    raw_token = secrets.token_urlsafe(32)
    agent.token_hash = hash_token(raw_token)
    agent.status = "approved"
    agent.approved_at = datetime.now(UTC)
    agent.approved_by_user_id = owner.id
    db.flush()
    record_event(
        db,
        "agent.approved",
        actor_user_id=owner.id,
        entity_type="agent",
        entity_id=str(agent.id),
    )
    return raw_token, agent


def list_agents(db: Session) -> list[Agent]:
    """Return all agents, newest first."""
    return list(db.scalars(select(Agent).order_by(Agent.created_at.desc())).all())
