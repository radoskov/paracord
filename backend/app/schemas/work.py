"""Work schemas."""

from pydantic import BaseModel


class WorkRead(BaseModel):
    id: str
    canonical_title: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    year: int | None = None
