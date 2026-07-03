"""Background job entrypoints.

Workers should be idempotent: retrying a failed extraction or summary job must not create duplicate
canonical records without review.
"""

import functools
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


def _record_job_event(event_type: str, job_name: str, *, error: str | None = None) -> None:
    """Persist a ``job.*`` audit event in its own short-lived session (best-effort, fail-open).

    A separate session keeps the lifecycle events durable even when the job's own transaction rolls
    back on failure; any error here is swallowed so job execution is never affected."""
    from app.db.session import SessionLocal
    from app.services.audit import record_event

    details: dict[str, Any] = {"job": job_name}
    if error is not None:
        details["error"] = error[:500]
    try:
        with SessionLocal() as db:
            record_event(db, event_type, entity_type="job", entity_id=job_name, details=details)
            db.commit()
    except Exception:  # noqa: BLE001 - job audit is best-effort; never break the job
        logger.warning("job audit event %s failed for %s", event_type, job_name, exc_info=True)


def _audited_job[JobT: Callable[..., Any]](func: JobT) -> JobT:
    """Emit ``job.started`` / ``job.completed`` / ``job.failed`` audit events around a job (§7.6)."""
    job_name = func.__name__

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        _record_job_event("job.started", job_name)
        try:
            result = func(*args, **kwargs)
        except Exception as exc:
            _record_job_event("job.failed", job_name, error=str(exc))
            raise
        _record_job_event("job.completed", job_name)
        return result

    return wrapper  # type: ignore[return-value]


@_audited_job
def extract_pdf_job(file_id: str, force_ocr: bool = False) -> None:
    """Run GROBID extraction for a PDF file, persist metadata/references, then enrich.

    ``force_ocr`` re-runs OCRmyPDF regardless of backend/quality (#22 manual Force OCR)."""
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
            extract_and_store(
                db,
                file=file,
                fetch_tei=client.process_fulltext_document_sync,
                force_ocr=force_ocr,
            )
        except Exception:
            # Persist a durable failure marker (so the UI can show extraction never succeeded)
            # before letting RQ record the job as failed. Roll back first: the session may hold a
            # half-applied extraction (or be in a failed transaction), which must not be committed.
            db.rollback()
            file = db.get(File, uuid.UUID(str(file_id)))
            if file is not None:
                file.status = "extract_failed"
                # Clear the owed-extraction marker: this is a terminal outcome, not "still owed"
                # (D7). The marker means "we haven't attempted a terminal extraction yet", so the
                # recovery sweep must not re-enqueue a file that already failed.
                file.extraction_requested_at = None
                db.commit()
            raise
        file.status = "extracted"
        file.extraction_requested_at = None  # terminal success — no longer owed (D7)
        link = db.scalar(select(FileWorkLink).where(FileWorkLink.file_id == file.id))
        work_id = str(link.work_id) if link else None
        # For index_and_extract uploads, discard the PDF now that extraction is stored —
        # only the Work, references and preview remain (SPEC §32.4).
        discard_after_extract(db, file=file, settings=settings)
        db.commit()
    # Chain external enrichment (best-effort; no-op when the work has no DOI/arXiv id).
    if work_id:
        enqueue_enrichment(work_id)


@_audited_job
def enrich_work_job(work_id: str) -> dict | None:
    """Enrich a work from external metadata sources (arXiv/Crossref), then (re)index its embedding.

    Returns the enrichment result (``sources`` / ``promoted`` / ``failed`` sources) so a partly
    failed run (e.g. arXiv down but Crossref fine, D8) is visible in the RQ job result.
    """
    import uuid

    from app.core.config import get_settings
    from app.db.session import SessionLocal
    from app.models.work import Work
    from app.services.metadata_enrichment import enrich_work
    from app.workers.queue import enqueue_chunking, enqueue_embedding

    result: dict | None = None
    with SessionLocal() as db:
        work = db.get(Work, uuid.UUID(str(work_id)))
        if work is None:
            return None
        try:
            result = enrich_work(db, work, settings=get_settings())
            db.commit()
        finally:
            # Chunk now that title/abstract/TEI are settled (chunks are the semantic-embedding
            # unit), then (re)index embeddings — both off the search read path. Runs even when
            # enrichment failed (offline / rate-limited) so the paper still gets indexed.
            enqueue_chunking(work_id)
            enqueue_embedding(work_id)
    return result


@_audited_job
def chunk_work_job(work_id: str) -> None:
    """(Re)build a work's passage chunks (HS1). Idempotent; no-op if the work is missing."""
    import uuid

    from app.db.session import SessionLocal
    from app.services.chunking import chunk_work_by_id

    with SessionLocal() as db:
        chunk_work_by_id(db, uuid.UUID(str(work_id)))
        db.commit()


@_audited_job
def embed_work_job(work_id: str) -> None:
    """(Re)index a work's embedding with the configured provider (keeps search read-only)."""
    import uuid

    from app.db.session import SessionLocal
    from app.models.work import Work
    from app.services.chunk_embeddings import embed_work_chunks
    from app.services.embeddings import get_embedding_provider
    from app.services.semantic_search import index_one_work

    with SessionLocal() as db:
        work = db.get(Work, uuid.UUID(str(work_id)))
        if work is None:
            return
        provider = get_embedding_provider(db=db)
        # Document-level baseline (hash-BOW default; works everywhere, keeps hybrid search working).
        index_one_work(db, work, provider=provider)
        # Chunk-level ANN upgrade — no-op unless the active model has a pgvector column (Postgres).
        embed_work_chunks(db, work, provider=provider)
        db.commit()


@_audited_job
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


@_audited_job
def reindex_embeddings_job() -> None:
    """Build embeddings for the active provider over every work missing one (WORKPLAN_NEXT 8F).

    Also backfills chunk-level embeddings for the active model (no-op unless a real model with a
    pgvector column is active on Postgres) — this is the backfill-on-activation path.
    """
    from app.db.session import SessionLocal
    from app.services.chunk_embeddings import backfill_chunk_embeddings
    from app.services.embedding_registry import register_provider
    from app.services.embeddings import get_embedding_provider
    from app.services.semantic_search import ensure_work_embeddings

    with SessionLocal() as db:
        provider = get_embedding_provider(db=db)
        ensure_work_embeddings(db, provider=provider, commit_every=50)
        # D22: provision the model's chunk-vector column + HNSW index in its OWN short transaction
        # and commit it BEFORE the long per-chunk backfill. The ALTER TABLE / CREATE INDEX take
        # heavy locks on work_chunks; committing them up front releases those locks so they aren't
        # held for the whole backfill job. The backfill below then finds the column already present
        # (register is idempotent) and only does per-chunk UPDATEs. No-op off Postgres.
        register_provider(db, provider)
        db.commit()
        backfill_chunk_embeddings(db, provider=provider, commit_every=200)
        db.commit()


@_audited_job
def rebuild_bm25_job() -> None:
    """Rebuild + persist the BM25F+ lexical index off the search read path (D13a).

    Enqueued when the corpus signature changes so a search never blocks on a rebuild; the worker
    rebuilds the sparse matrix and overwrites the on-disk copy that the API processes mmap-load.
    """
    from app.db.session import SessionLocal
    from app.services.bm25_index import rebuild_persisted_index

    with SessionLocal() as db:
        rebuild_persisted_index(db)


@_audited_job
def pull_model_job(provider: str, model: str) -> None:
    """Download/pull an AI model (Ollama / sentence-transformers). Raises on failure (8C)."""
    from app.db.session import SessionLocal
    from app.services.ai_config import get_ai_config
    from app.services.model_management import pull_model

    with SessionLocal() as db:
        ollama_url = get_ai_config(db).ollama_url
    pull_model(provider, model, ollama_url=ollama_url)


@_audited_job
def topic_work_job(work_id: str) -> None:
    """Run per-paper topic modeling for a work and persist ``work.topics`` (Phase K).

    Idempotent and a no-op if the work is missing. Honors the admin-configured topic backend for
    provenance; the ranking is the deterministic single-doc baseline.
    """
    import uuid

    from app.db.session import SessionLocal
    from app.models.work import Work
    from app.services.ai_config import get_ai_config
    from app.services.topic_modeling import extract_paper_topics

    with SessionLocal() as db:
        work = db.get(Work, uuid.UUID(str(work_id)))
        if work is None:
            return
        config = get_ai_config(db)
        work.topics = extract_paper_topics(
            db,
            work=work,
            backend=config.topic_backend,
            embedding_model=config.topic_embedding_model,
        )
        db.commit()


@_audited_job
def keywords_work_job(work_id: str) -> None:
    """Re-run keyword extraction for a work over abstract + latest stored TEI body (Phase K).

    Falls back to title + abstract when no TEI body is stored. Idempotent; no-op if work missing.
    """
    import uuid

    from sqlalchemy import select

    from app.db.session import SessionLocal
    from app.models.citation import RawTeiDocument
    from app.models.work import Work
    from app.services.keyword_extraction import extract_keywords
    from app.services.tei_parser import extract_body_text

    with SessionLocal() as db:
        work = db.get(Work, uuid.UUID(str(work_id)))
        if work is None:
            return
        body = ""
        tei = db.scalar(
            select(RawTeiDocument)
            .where(RawTeiDocument.work_id == work.id)
            .order_by(RawTeiDocument.created_at.desc())
        )
        if tei is not None:
            body = extract_body_text(tei.tei_xml) or ""
        source = " ".join(part for part in (work.canonical_title, work.abstract, body) if part)
        work.keywords = extract_keywords(source, top_k=12)
        db.commit()


def summarize_scope_job(scope_type: str, scope_id: str | None = None) -> None:
    """Run local summary pipeline for a scope."""
    _ = (scope_type, scope_id)


def topic_model_job(scope_type: str, scope_id: str | None = None) -> None:
    """Run topic modeling pipeline for a scope."""
    _ = (scope_type, scope_id)
