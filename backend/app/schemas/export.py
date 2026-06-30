"""Export schemas."""

from pydantic import AliasChoices, BaseModel, Field


class ExportRequest(BaseModel):
    scope_type: str = Field(validation_alias=AliasChoices("scope_type", "target_type"))
    scope_id: str | None = None
    target_id: str | None = None
    # For scope_type 'selection' / 'search': the explicit set of works to export.
    work_ids: list[str] | None = None
    format: str
    style: str | None = None


class ExportResponse(BaseModel):
    filename: str
    content_type: str
    content: str
