"""Export schemas."""

from pydantic import BaseModel


class ExportRequest(BaseModel):
    scope_type: str
    scope_id: str | None = None
    format: str
    style: str | None = None


class ExportResponse(BaseModel):
    filename: str
    content_type: str
    content: str
