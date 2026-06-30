"""Pydantic schemas for the admin group / grant / default-grant / access-settings API (Phase H)."""

import uuid
from datetime import datetime

from pydantic import BaseModel


class GroupCreate(BaseModel):
    name: str


class GroupOut(BaseModel):
    id: uuid.UUID
    name: str
    is_personal: bool
    personal_user_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class GroupMemberOut(BaseModel):
    id: uuid.UUID
    username: str
    role: str
    display_name: str | None = None

    model_config = {"from_attributes": True}


class MembershipAdd(BaseModel):
    user_id: uuid.UUID


class GrantAdd(BaseModel):
    target_type: str  # 'rack' | 'shelf'
    target_id: uuid.UUID


class GrantOut(BaseModel):
    id: uuid.UUID
    group_id: uuid.UUID
    target_type: str
    target_id: uuid.UUID
    created_at: datetime

    model_config = {"from_attributes": True}


class DefaultGrantAdd(BaseModel):
    target_type: str  # 'rack' | 'shelf'
    target_id: uuid.UUID


class DefaultGrantOut(BaseModel):
    id: uuid.UUID
    target_type: str
    target_id: uuid.UUID
    created_at: datetime

    model_config = {"from_attributes": True}


class AccessSettingsOut(BaseModel):
    default_access_level: str
    allowed: list[str]


class AccessSettingsUpdate(BaseModel):
    default_access_level: str
