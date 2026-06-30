"""Background job entrypoints.

Workers should be idempotent: retrying a failed extraction or summary job must not create duplicate
canonical records without review.
"""


def extract_pdf_job(file_id: str) -> None:
    """Run GROBID extraction for a PDF file, persist metadata/references, then enrich."""
    import uuid

    from sqlalchemy import select

    from app.core.config import get_settings
    from app.db.session import SessionLocal
    from app.models.file import File, FileWorkLink
    from app.services.agent_files import discard_after_extract
    from app.services.extraction import extract_and_store
    from app.services.grobid_client import GrobidClient
    from app.workers.queue import enqueue_enrichment

    settings = get_settings()
    client = GrobidClient(settings.grobid_url, settings=settings)
    work_id = None
    with SessionLocal() as db:
        file = db.get(File, uuid.UUID(str(file_id)))
        if file is None:
            return
        try:
            extract_and_store(db, file=file, fetch_tei=client.process_fulltext_document_sync)
        except Exception:
            # Persist a durable failure marker (so the UI can show extraction never succeeded)
            # before letting RQ record the job as failed.
            file.status = "extract_failed"
            db.commit()
            raise
        file.status = "extracted"
        link = db.scalar(select(FileWorkLink).where(FileWorkLink.file_id == file.id))
        work_id = str(link.work_id) if link else None
        # For index_and_extract uploads, discard the PDF now that extraction is stored —
        # only the Work, references and preview remain (SPEC §32.4).
        discard_after_extract(db, file=file, settings=settings)
        db.commit()
    # Chain external enrichment (best-effort; no-op when the work has no DOI/arXiv id).
    if work_id:
        enqueue_enrichment(work_id)


def enrich_work_job(work_id: str) -> None:
    """Enrich a work from external metadata sources (arXiv/Crossref), then (re)index its embedding."""
    import uuid

    from app.core.config import get_settings
    from app.db.session import SessionLocal
    from app.models.work import Work
    from app.services.metadata_enrichment import enrich_work
    from app.workers.queue import enqueue_embedding

    with SessionLocal() as db:
        work = db.get(Work, uuid.UUID(str(work_id)))
        if work is None:
            return
        enrich_work(db, work, settings=get_settings())
        db.commit()
    # Embeddings are built off the search read path; (re)index now that title/abstract are settled.
    enqueue_embedding(work_id)


def embed_work_job(work_id: str) -> None:
    """(Re)index a work's embedding with the configured provider (keeps search read-only)."""
    import uuid

    from app.core.config import get_settings
    from app.db.session import SessionLocal
    from app.models.work import Work
    from app.services.embeddings import get_embedding_provider
    from app.services.semantic_search import index_one_work

    with SessionLocal() as db:
        work = db.get(Work, uuid.UUID(str(work_id)))
        if work is None:
            return
        index_one_work(db, work, provider=get_embedding_provider(get_settings()))
        db.commit()


def scan_duplicates_job() -> None:
    """Full-library duplicate/version scan over every work and file (off the request path)."""
    from sqlalchemy import select

    from app.db.session import SessionLocal
    from app.models.file import File
    from app.models.work import Work
    from app.services.duplicate_detection import scan_duplicate_candidates

    with SessionLocal() as db:
        for work in db.scalars(select(Work)).all():
            scan_duplicate_candidates(db, work=work)
        for file in db.scalars(select(File)).all():
            scan_duplicate_candidates(db, file=file)
        db.commit()


def summarize_scope_job(scope_type: str, scope_id: str | None = None) -> None:
    """Run local summary pipeline for a scope."""
    _ = (scope_type, scope_id)


def topic_model_job(scope_type: str, scope_id: str | None = None) -> None:
    """Run topic modeling pipeline for a scope."""
    _ = (scope_type, scope_id)
