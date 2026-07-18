"""Saved-filter schemas (Phase B7)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class SavedFilterParams(BaseModel):
    """The structured (non-free-text) part of a saved Library query.

    Mirrors the explicit query params ``list_works``/``build_works_query`` accept. ``missing`` is a
    list of Work field names to filter on absence of (``title``/``abstract``/``year``/…).
    """

    reading_status: str | None = None
    shelf_id: uuid.UUID | None = None
    rack_id: uuid.UUID | None = None
    row_id: uuid.UUID | None = None
    tag_id: uuid.UUID | None = None
    has_pdf: bool | None = None
    has_references: bool | None = None
    missing: list[str] = []


class SavedFilterCreate(BaseModel):
    """Body to save a new named Library query."""

    name: str
    search_mode: Literal["metadata", "semantic", "hybrid"] = "metadata"
    query_text: str | None = None
    params: SavedFilterParams = SavedFilterParams()


class SavedFilterUpdate(BaseModel):
    """Partial update to a saved filter; omitted fields are left unchanged."""

    name: str | None = None
    search_mode: Literal["metadata", "semantic", "hybrid"] | None = None
    query_text: str | None = None
    params: SavedFilterParams | None = None


class SavedFilterRead(BaseModel):
    """A saved filter as returned by the API."""

    id: uuid.UUID
    name: str
    search_mode: str
    query_text: str | None = None
    params: SavedFilterParams = SavedFilterParams()
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
