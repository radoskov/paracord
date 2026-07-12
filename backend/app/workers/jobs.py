"""Background job entrypoints.

Workers should be idempotent: retrying a failed extraction or summary job must not create duplicate
canonical records without review.
"""

import functools
import logging
from collections.abc import Callable
from typing import Any

from app.services.doi_conflict import conflict_message, doi_conflict_detail, doi_from_detail

logger = logging.getLogger(__name__)

# Batch size for the full-library rescan sweep (S8): rows matched+committed per transaction.
_RESCAN_COMMIT_EVERY = 500


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


def _record_doi_conflict(*, entity_type: str, entity_id: str, detail: str, phase: str) -> None:
    """Persist a ``metadata.doi_conflict`` audit event (own session, best-effort, fail-open)."""
    from app.db.session import SessionLocal
    from app.services.audit import record_event

    try:
        with SessionLocal() as db:
            record_event(
                db,
                "metadata.doi_conflict",
                entity_type=entity_type,
                entity_id=entity_id,
                details={"phase": phase, "detail": detail[:500]},
            )
            db.commit()
    except Exception:  # noqa: BLE001 - audit is best-effort; never break the job
        logger.warning("doi_conflict audit failed for %s %s", entity_type, entity_id, exc_info=True)


def _set_work_processing_error(work_id: str, stage: str, message: str) -> None:
    """Record a per-paper processing error for ``stage`` (F2) so it shows on the paper as a badge.

    Own short-lived session, best-effort (never breaks the job). Overwrites any prior error for the
    paper — the badge reflects the most recent failed stage.
    """
    import uuid

    from app.db.session import SessionLocal
    from app.models.work import Work

    try:
        with SessionLocal() as db:
            work = db.get(Work, uuid.UUID(str(work_id)))
            if work is not None:
                work.processing_error = f"{stage}: {message}".strip()[:500]
                db.commit()
    except Exception:  # noqa: BLE001 - the indicator is best-effort; never break the job
        logger.warning("could not set processing_error for work %s", work_id, exc_info=True)


def _clear_work_processing_error(work_id: str, stage: str) -> None:
    """Clear a per-paper processing error, but only if it belongs to ``stage`` (F2).

    Stage-scoped so a successful keyword/topic re-run can't erase an outstanding enrich failure.
    Own session, best-effort.
    """
    import uuid

    from app.db.session import SessionLocal
    from app.models.work import Work

    try:
        with SessionLocal() as db:
            work = db.get(Work, uuid.UUID(str(work_id)))
            if (
                work is not None
                and work.processing_error is not None
                and work.processing_error.startswith(f"{stage}:")
            ):
                work.processing_error = None
                db.commit()
    except Exception:  # noqa: BLE001 - best-effort
        logger.warning("could not clear processing_error for work %s", work_id, exc_info=True)


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
    from sqlalchemy.exc import IntegrityError, OperationalError

    from app.core.config import get_settings
    from app.db.session import SessionLocal
    from app.models.file import MAX_EXTRACTION_ATTEMPTS, File, FileWorkLink
    from app.services.agent_files import discard_after_extract
    from app.services.extraction import extract_and_store
    from app.services.grobid_client import GrobidClient, GrobidUnavailableError
    from app.workers.queue import enqueue_enrichment

    def _disable_retry() -> None:
        """Stop RQ retrying this job (terminal / cap reached) so it goes straight to failed.

        RQ retries a job iff its ``retries_left`` is > 0 after it raises; zeroing it means a known-
        terminal failure (DOI conflict, corrupt PDF, cap reached) is not re-run — it still raises so
        it lands in the failed-jobs list. A no-op when not running under a worker (inline / tests).
        """
        try:
            from rq import get_current_job

            job = get_current_job()
            if job is not None:
                job.retries_left = 0
        except Exception:  # noqa: BLE001 - no worker context (inline/tests): nothing to disable
            pass

    settings = get_settings()
    client = GrobidClient(settings.grobid_url, settings=settings)
    work_id = None
    with SessionLocal() as db:
        file = db.get(File, uuid.UUID(str(file_id)))
        if file is None:
            return

        # Count this attempt durably up front (survives a hard worker crash) so the cap bounds
        # retries across restarts, not just within one RQ job's in-Redis retry budget (F2).
        file.extraction_attempts = (file.extraction_attempts or 0) + 1
        attempts = file.extraction_attempts
        db.commit()

        def _mark_failed() -> None:
            # Terminal outcome: persist a durable failure status and CLEAR the owed marker so the
            # recovery sweep won't re-enqueue it. Roll back first — the session may hold a
            # half-applied extraction / a failed transaction that must not commit.
            db.rollback()
            f = db.get(File, uuid.UUID(str(file_id)))
            if f is not None:
                f.status = "extract_failed"
                f.extraction_requested_at = None
                db.commit()

        try:
            # The whole write path is inside the try so a deferred flush (e.g. the DOI unique
            # violation, issue 6) is caught here rather than surfacing unguarded after the block.
            extract_and_store(
                db,
                file=file,
                fetch_tei=client.process_fulltext_document_sync,
                force_ocr=force_ocr,
            )
            file.status = "extracted"
            file.extraction_requested_at = None  # terminal success — no longer owed (D7)
            file.extraction_attempts = 0  # clean slate for any future re-extract
            link = db.scalar(select(FileWorkLink).where(FileWorkLink.file_id == file.id))
            work_id = str(link.work_id) if link else None
            # For index_and_extract uploads, discard the PDF now that extraction is stored —
            # only the Work, references and preview remain (SPEC §32.4).
            discard_after_extract(db, file=file, settings=settings)
            db.commit()
        except (GrobidUnavailableError, OperationalError):
            # TRANSIENT (GROBID unreachable / DB blip). Under the cap: keep the owed marker set and
            # re-raise so RQ retries automatically (the recovery sweep is the cross-restart backstop).
            # At the cap: give up as terminal so retries can't loop forever.
            db.rollback()
            if attempts >= MAX_EXTRACTION_ATTEMPTS:
                _mark_failed()
                _disable_retry()
            raise
        except IntegrityError as exc:
            # TERMINAL (e.g. a DOI unique-violation) — retrying can't help. Mark failed and stop RQ
            # from retrying, but still raise so it is visible in the failed-jobs list.
            detail = doi_conflict_detail(exc)
            _mark_failed()
            _disable_retry()
            if detail is not None:
                # Record a clear audit event and re-raise a concise message (not the raw SQL) naming
                # the offending DOI and the paper that holds it. _mark_failed committed, so the
                # session is clean for the existing-paper lookup inside conflict_message.
                _record_doi_conflict(
                    entity_type="file", entity_id=str(file_id), detail=detail, phase="extract"
                )
                raise RuntimeError(conflict_message(db, doi=doi_from_detail(detail))) from None
            raise
        except Exception:
            # TERMINAL (unexpected). Mark failed and don't retry, but surface it as failed.
            _mark_failed()
            _disable_retry()
            raise
    # Chain external enrichment (best-effort; no-op when the work has no DOI/arXiv id).
    if work_id:
        enqueue_enrichment(work_id)


@_audited_job
def extract_staging_item_job(item_id: str) -> None:
    """Record-free GROBID extraction for one staged multi-import PDF (batch10 #1).

    Extracts the staged PDF and records the parsed metadata + TEI + collisions on the item (no Work
    yet). When this completes the last item of its batch, the batch flips to ``ready``; a
    ``direct``-mode batch then auto-commits (accept extracted, non-blocked items) in the same step.
    """
    import uuid

    from sqlalchemy import select

    from app.core.config import get_settings
    from app.db.session import SessionLocal
    from app.models.import_staging import ImportStagingBatch, ImportStagingItem
    from app.models.user import User
    from app.services import import_staging
    from app.services.grobid_client import GrobidClient

    settings = get_settings()
    client = GrobidClient(settings.grobid_url, settings=settings)
    commit_summary = None
    with SessionLocal() as db:
        item = db.get(ImportStagingItem, uuid.UUID(str(item_id)))
        if item is None:
            return
        import_staging.extract_staging_item(
            db, item=item, fetch_tei=client.process_fulltext_document_sync, settings=settings
        )
        batch = db.get(ImportStagingBatch, item.batch_id)
        actor = db.get(User, batch.created_by_user_id) if batch else None
        if (
            batch is not None
            and actor is not None
            and import_staging.finalize_if_ready(db, batch)
            and batch.mode == "direct"
        ):
            items = list(
                db.scalars(
                    select(ImportStagingItem).where(ImportStagingItem.batch_id == batch.id)
                ).all()
            )
            decisions = import_staging.auto_decisions(items)
            commit_summary = import_staging.commit_staging(
                db, actor=actor, batch=batch, decisions=decisions, settings=settings
            )
        db.commit()
    # Post-commit background jobs run outside the session (they read committed rows).
    if commit_summary is not None:
        import_staging.enqueue_post_commit_jobs(commit_summary)


@_audited_job
def enrich_work_job(work_id: str) -> dict | None:
    """Enrich a work from external metadata sources (arXiv/Crossref), then (re)index its embedding.

    Returns the enrichment result (``sources`` / ``promoted`` / ``failed`` sources) so a partly
    failed run (e.g. arXiv down but Crossref fine, D8) is visible in the RQ job result.
    """
    import uuid

    from sqlalchemy.exc import IntegrityError

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
            if result.get("failed") and not result.get("sources"):
                # Every planned source failed (enrich_work swallows per-source errors so a partial
                # outage can't abort the rest). A total failure is almost always transient
                # (rate-limit, network), so raise: the generic handler below flags the paper and
                # the re-raise lets the RQ retry (S6) re-run the job.
                raise RuntimeError(
                    "enrichment failed for every source: " + ", ".join(result["failed"])
                )
            db.commit()
            _clear_work_processing_error(
                work_id, "enrich"
            )  # succeeded — clear any prior enrich error
        except IntegrityError as exc:
            # A DOI from an external source collided with another paper's DOI (issue 6). Enrichment
            # is best-effort and not swept by D7, so this is non-fatal: roll back, record a clear
            # audit event, flag the paper, and let it keep the data it already had (chunk/embed run).
            detail = doi_conflict_detail(exc)
            db.rollback()
            if detail is None:
                _set_work_processing_error(work_id, "enrich", "database integrity error")
                raise
            _record_doi_conflict(
                entity_type="work", entity_id=str(work_id), detail=detail, phase="enrich"
            )
            message = conflict_message(db, doi=doi_from_detail(detail))
            _set_work_processing_error(work_id, "enrich", message)
            result = {"error": "doi_conflict", "detail": message}
        except Exception as exc:
            # Unexpected enrichment failure: flag the paper (loud, per-paper) and re-raise so it also
            # lands in the failed-jobs list / job.failed audit. chunk+embed still run (finally).
            db.rollback()
            _set_work_processing_error(work_id, "enrich", str(exc))
            raise
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
        for work in db.scalars(select(Work).where(Work.merged_into_id.is_(None))).all():
            scan_duplicate_candidates(db, work=work)
        for file in db.scalars(select(File)).all():
            scan_duplicate_candidates(db, file=file)
        db.commit()


def rescan_reference_matches_job() -> None:
    """Full-library reference→work rematch over every reference (batch 12, off the request path).

    Respects the batch-12 status rules per reference (confirmed locked; rejected not re-proposed) and
    uses the current ``use_fuzzy_match_as_confirmed`` toggle. Also rematches every cached external
    citing paper (the incoming direction — same matcher).

    S8/S9: the library's works, identifier/blocking keys, and author names are loaded ONCE into
    in-memory indexes (hard assumption: they fit in RAM), so each row is matched with dict lookups
    instead of 2-3 SQL point queries. Commits land every ``_RESCAN_COMMIT_EVERY`` rows and each row
    is individually guarded — a crash/poison row loses at most one batch, and a rerun (idempotent)
    finishes the rest.
    """
    from sqlalchemy import select

    from app.db.session import SessionLocal
    from app.models.citation import Reference
    from app.models.external_citation import ExternalPaper
    from app.services.app_config import effective_use_fuzzy_match_as_confirmed
    from app.services.citing_papers import resolve_external_paper
    from app.services.reference_matching import (
        build_match_indexes,
        candidates_from_indexes,
        resolve_and_persist,
    )
    from app.utils.normalization import normalize_title

    def _id_batches(ids: list) -> list[list]:
        return [ids[i : i + _RESCAN_COMMIT_EVERY] for i in range(0, len(ids), _RESCAN_COMMIT_EVERY)]

    with SessionLocal() as db:
        # The in-memory indexes hold ORM Work rows across many commits; default expire-on-commit
        # would turn every post-commit attribute read into a refresh query (defeating S8).
        db.expire_on_commit = False
        fuzzy = effective_use_fuzzy_match_as_confirmed(db)
        indexes = build_match_indexes(db)

        # Ids first, rows per batch: a mid-iteration commit would kill a streaming cursor, and a
        # crashed run loses at most the current (uncommitted) batch — the rerun is idempotent and
        # skips already-settled rows cheaply.
        for batch in _id_batches(db.scalars(select(Reference.id)).all()):
            for reference in db.scalars(select(Reference).where(Reference.id.in_(batch))).all():
                candidates = candidates_from_indexes(
                    indexes,
                    doi=reference.doi,
                    arxiv_id=reference.arxiv_id,
                    normalized_title=reference.normalized_title
                    or (normalize_title(reference.title) if reference.title else None),
                )
                try:
                    resolve_and_persist(
                        db,
                        reference,
                        fuzzy_as_confirmed=fuzzy,
                        candidate_works=candidates,
                        author_names=indexes.author_names,
                    )
                except Exception:  # noqa: BLE001 - one poisoned row must not kill the sweep
                    logger.warning("reference rescan failed for %s", reference.id, exc_info=True)
                    db.rollback()
            db.commit()

        for batch in _id_batches(db.scalars(select(ExternalPaper.id)).all()):
            for external in db.scalars(
                select(ExternalPaper).where(ExternalPaper.id.in_(batch))
            ).all():
                candidates = candidates_from_indexes(
                    indexes,
                    doi=external.doi,
                    arxiv_id=external.arxiv_id,
                    normalized_title=normalize_title(external.title) if external.title else None,
                )
                try:
                    resolve_external_paper(
                        db,
                        external,
                        candidate_works=candidates,
                        author_names=indexes.author_names,
                        clear_on_miss=True,
                    )
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "external-paper rescan failed for %s", external.id, exc_info=True
                    )
                    db.rollback()
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
        try:
            config = get_ai_config(db)
            work.topics = extract_paper_topics(
                db,
                work=work,
                backend=config.topic_backend,
                embedding_model=config.topic_embedding_model,
            )
            db.commit()
            _clear_work_processing_error(work_id, "topics")
        except Exception as exc:
            db.rollback()
            _set_work_processing_error(work_id, "topics", str(exc))
            raise


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
    from app.services.tei_parser import extract_body_text, extract_sections

    with SessionLocal() as db:
        work = db.get(Work, uuid.UUID(str(work_id)))
        if work is None:
            return
        try:
            body = ""
            headings = ""
            tei = db.scalar(
                select(RawTeiDocument)
                .where(RawTeiDocument.work_id == work.id)
                .order_by(RawTeiDocument.created_at.desc())
            )
            if tei is not None:
                body = extract_body_text(tei.tei_xml) or ""
                headings = " ".join(label for label, _ in extract_sections(tei.tei_xml) if label)
            source = " ".join(part for part in (work.canonical_title, work.abstract, body) if part)
            # Boost phrases that also appear in the title / abstract / section headings (issue 8).
            boost = " ".join(
                part for part in (work.canonical_title, work.abstract, headings) if part
            )
            work.keywords = extract_keywords(source, top_k=12, boost_text=boost)
            db.commit()
            _clear_work_processing_error(work_id, "keywords")
        except Exception as exc:
            db.rollback()
            _set_work_processing_error(work_id, "keywords", str(exc))
            raise


@_audited_job
def summarize_scope_job(
    scope_type: str,
    scope_id: str | None = None,
    summary_type: str | None = None,
    max_sentences: int = 8,
    model_name: str | None = None,
    actor_user_id: str | None = None,
) -> dict | None:
    """Run the scope-summary pipeline off the request path (S15).

    Enqueued when the scope exceeds the ``ai_scope_job_threshold`` (S16). Runs with the
    *requesting user's* visibility (recomputed here from ``actor_user_id``) so the stored summary
    matches what the inline path would have produced; aborts if that user is gone.
    """
    import uuid

    from app.db.session import SessionLocal
    from app.models.user import User
    from app.services import access
    from app.services.summarization import summarize_scope

    with SessionLocal() as db:
        actor = db.get(User, uuid.UUID(actor_user_id)) if actor_user_id else None
        if actor is None:
            return None
        summary, work_count = summarize_scope(
            db,
            scope_type=scope_type,
            scope_id=uuid.UUID(scope_id) if scope_id else None,
            summary_type=summary_type or "extractive",
            max_sentences=max_sentences,
            model_name=model_name,
            created_by_user_id=actor.id,
            visible_ids=access.visible_work_ids(db, actor),
        )
        db.commit()
        return {"summary_id": str(summary.id), "work_count": work_count}


@_audited_job
def topic_model_job(
    scope_type: str,
    scope_id: str | None = None,
    max_topics: int = 5,
    backend: str | None = None,
    embedding_model: str | None = None,
    actor_user_id: str | None = None,
) -> dict | None:
    """Run the scope topic-model pipeline off the request path (S15).

    Stores TopicAssignment rows exactly like the inline path (same visibility, recomputed from
    ``actor_user_id``); the UI reads them back through the topic graph.
    """
    import uuid

    from app.db.session import SessionLocal
    from app.models.user import User
    from app.services import access
    from app.services.ai_config import get_ai_config
    from app.services.topic_modeling import model_topics

    with SessionLocal() as db:
        actor = db.get(User, uuid.UUID(actor_user_id)) if actor_user_id else None
        if actor is None:
            return None
        cfg = get_ai_config(db)
        result = model_topics(
            db,
            scope_type=scope_type,
            scope_id=uuid.UUID(scope_id) if scope_id else None,
            max_topics=max(1, min(max_topics, 20)),
            backend=backend or cfg.topic_backend,
            embedding_model=embedding_model or cfg.topic_embedding_model,
            visible_ids=access.visible_work_ids(db, actor),
        )
        db.commit()
        return {"model_id": result.get("model_id"), "work_count": result.get("work_count")}
