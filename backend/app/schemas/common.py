"""Common API schemas."""

from pydantic import BaseModel


class Message(BaseModel):
    """Simple message response."""

    message: str


class HealthResponse(BaseModel):
    """Health response."""

    status: str
    service: str = "paperracks-api"
    version: str = "0.0.0"
