"""File and PDF access endpoints."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/{file_id}/pdf")
def stream_pdf(file_id: str) -> dict[str, str]:
    """Stream a PDF after checking authentication and file permissions."""
    return {"status": "todo", "file_id": file_id}
