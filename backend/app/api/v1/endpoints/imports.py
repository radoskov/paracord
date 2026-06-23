"""Import pipeline endpoints."""

from fastapi import APIRouter

router = APIRouter()


@router.post("")
def create_import_batch() -> dict[str, str]:
    """Create an import batch from PDFs, URLs, DOI/arXiv IDs, or bibliography files."""
    return {"status": "todo"}
