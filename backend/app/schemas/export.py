"""Export schemas."""

from pydantic import AliasChoices, BaseModel, Field


class ExportRequest(BaseModel):
    """Request to export a set of works to a citation file format (e.g. BibTeX, RIS).

    ``scope_type`` selects what ``scope_id``/``target_id``/``work_ids`` refer to (e.g. a single
    work, a shelf/rack, or an explicit selection/search result set).
    """

    scope_type: str = Field(validation_alias=AliasChoices("scope_type", "target_type"))
    scope_id: str | None = None
    target_id: str | None = None
    # For scope_type 'selection' / 'search': the explicit set of works to export.
    work_ids: list[str] | None = None
    format: str
    style: str | None = None
    # Optional per-work citation-key overrides: {work_id: key}.
    citation_keys: dict[str, str] | None = None


class ExportResponse(BaseModel):
    """Rendered export file, returned inline (not streamed) with its content type/filename."""

    filename: str
    content_type: str
    content: str
