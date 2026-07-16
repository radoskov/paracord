"""Pydantic schemas for the admin group / grant / default-grant / access-settings API (Phase H)."""

import uuid
from datetime import datetime

from pydantic import BaseModel


class GroupCreate(BaseModel):
    """Body to create a new (non-personal) access group."""

    name: str


class GroupOut(BaseModel):
    """A group as returned by the API, including whether it is a user's implicit personal group."""

    id: uuid.UUID
    name: str
    is_personal: bool
    personal_user_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class GroupMemberOut(BaseModel):
    """A user as listed among a group's members."""

    id: uuid.UUID
    username: str
    role: str
    display_name: str | None = None

    model_config = {"from_attributes": True}


class MembershipAdd(BaseModel):
    """Add a user to a group."""

    user_id: uuid.UUID


class GrantAdd(BaseModel):
    """Grant a group access to a rack or shelf."""

    target_type: str  # 'rack' | 'shelf'
    target_id: uuid.UUID


class GrantOut(BaseModel):
    """An access grant of a rack/shelf to a group, as returned by the API."""

    id: uuid.UUID
    group_id: uuid.UUID
    target_type: str
    target_id: uuid.UUID
    created_at: datetime

    model_config = {"from_attributes": True}


class DefaultGrantAdd(BaseModel):
    """Add a rack/shelf to the set granted by default to every newly created user's personal group."""

    target_type: str  # 'rack' | 'shelf'
    target_id: uuid.UUID


class DefaultGrantOut(BaseModel):
    """A default-grant entry as returned by the API."""

    id: uuid.UUID
    target_type: str
    target_id: uuid.UUID
    created_at: datetime

    model_config = {"from_attributes": True}


class AccessSettingsOut(BaseModel):
    """Site-wide default access-control settings."""

    default_access_level: str
    allowed: list[str]


class AccessSettingsUpdate(BaseModel):
    """Update the site-wide default access level."""

    default_access_level: str
