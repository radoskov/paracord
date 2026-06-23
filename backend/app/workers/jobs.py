"""Background job entrypoints.

Workers should be idempotent: retrying a failed extraction or summary job must not create duplicate
canonical records without review.
"""


def extract_pdf_job(file_id: str) -> None:
    """Run GROBID extraction for a PDF file."""
    _ = file_id


def summarize_scope_job(scope_type: str, scope_id: str | None = None) -> None:
    """Run local summary pipeline for a scope."""
    _ = (scope_type, scope_id)


def topic_model_job(scope_type: str, scope_id: str | None = None) -> None:
    """Run topic modeling pipeline for a scope."""
    _ = (scope_type, scope_id)
