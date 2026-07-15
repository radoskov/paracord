"""User-management API schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.core.security import Role


class UserCreate(BaseModel):
    username: str
    password: str
    role: Role = Role.READER


class UserRoleUpdate(BaseModel):
    role: Role


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    role: str
    created_at: datetime
    disabled_at: datetime | None = None
    is_bootstrap: bool = False
    # Whether this user's downloads may use the server's Elsevier API key (UX batch 3; NULL→False).
    elsevier_api_allowed: bool | None = False
