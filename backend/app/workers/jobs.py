"""Background job entrypoints.

Workers should be idempotent: retrying a failed extraction or summary job must not create duplicate
canonical records without review.
"""


def extract_pdf_job(file_id: str) -> None:
    """Run GROBID extraction for a PDF file and persist metadata/references."""
    import uuid

    from app.core.config import get_settings
    from app.db.session import SessionLocal
    from app.models.file import File
    from app.services.extraction import extract_and_store
    from app.services.grobid_client import GrobidClient

    client = GrobidClient(get_settings().grobid_url)
    with SessionLocal() as db:
        file = db.get(File, uuid.UUID(str(file_id)))
        if file is None:
            return
        extract_and_store(db, file=file, fetch_tei=client.process_fulltext_document_sync)
        db.commit()


def summarize_scope_job(scope_type: str, scope_id: str | None = None) -> None:
    """Run local summary pipeline for a scope."""
    _ = (scope_type, scope_id)


def topic_model_job(scope_type: str, scope_id: str | None = None) -> None:
    """Run topic modeling pipeline for a scope."""
    _ = (scope_type, scope_id)
