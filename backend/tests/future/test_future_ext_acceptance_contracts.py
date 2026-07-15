"""Acceptance contracts that graduated from the "future" skip-list (UX batch 4 review).

Of the four original placeholder contracts, three were already enforced by the routine suite and
were dropped as redundant rather than re-implemented here:

* GROBID coordinates → reader overlay boxes: ``test_extraction.py`` (TEI fixture round-trip incl.
  multi-box mentions + ``test_parse_coords_handles_malformed``).
* Local-LLM summary provenance (model/prompt/fallback): ``test_summarization.py``
  (``test_summarize_work_local_llm_falls_back_to_extractive_with_provenance``,
  ``test_summarize_work_persists_provenance_columns``, ``test_summary_api_returns_provenance``).
* Agent teleport SHA round-trip + local-path hiding: ``test_agent_teleport_acceptance.py``.

The remaining contract — stable, scope-aware topic modeling so topics can be compared across
scopes — is implemented below against the guarantees the modeler actually makes: deterministic
per-scope model ids, per-scope assignment isolation, and re-run stability.
"""

from __future__ import annotations

from app.models.ai import TopicAssignment
from app.models.organization import Shelf, ShelfWork
from app.models.work import Work
from app.services.topic_modeling import model_topics
from sqlalchemy import select

WORKS = [
    ("Graph neural networks for molecules", "Message passing over molecular graphs."),
    ("Molecular property prediction", "Predicting chemistry properties with graph networks."),
    ("Transformers for language", "Attention-based sequence modeling for text."),
    ("Large language models", "Scaling attention language models to many tasks."),
]


def _seed(db) -> Shelf:
    shelf = Shelf(name="compare-scope")
    db.add(shelf)
    db.flush()
    for title, abstract in WORKS:
        work = Work(canonical_title=title, normalized_title=title.lower(), abstract=abstract)
        db.add(work)
        db.flush()
        db.add(ShelfWork(shelf_id=shelf.id, work_id=work.id))
    db.commit()
    return shelf


def test_topic_models_are_scope_namespaced_stable_and_comparable(db) -> None:
    """Modeling the same papers under different scopes yields deterministic, non-interfering
    models whose topics can be compared across scopes."""
    shelf = _seed(db)

    shelf_result = model_topics(db, scope_type="shelf", scope_id=shelf.id, max_topics=2)
    library_result = model_topics(db, scope_type="library", scope_id=None, max_topics=2)
    db.commit()

    # Deterministic scope-namespaced ids — the comparison key across scopes.
    assert shelf_result["model_id"] == f"keyword-kmeans:shelf:{shelf.id}"
    assert library_result["model_id"] == "keyword-kmeans:library:all"

    # Same papers → the same deterministic clustering in both scopes (comparable topics).
    def _signature(result) -> set[tuple[str, ...]]:
        return {tuple(sorted(t["work_ids"])) for t in result["topics"]}

    assert _signature(shelf_result) == _signature(library_result)

    # Assignments are isolated per model: both coexist, one row per (model, work).
    def _assignments(model_id: str) -> list[TopicAssignment]:
        return list(
            db.scalars(
                select(TopicAssignment).where(TopicAssignment.topic_model_id == model_id)
            ).all()
        )

    assert len(_assignments(shelf_result["model_id"])) == len(WORKS)
    assert len(_assignments(library_result["model_id"])) == len(WORKS)

    # Re-running one scope replaces ONLY its own assignments; the other scope is untouched.
    library_before = {(a.work_id, a.topic_id) for a in _assignments("keyword-kmeans:library:all")}
    rerun = model_topics(db, scope_type="shelf", scope_id=shelf.id, max_topics=2)
    db.commit()
    assert _signature(rerun) == _signature(shelf_result)  # stable across re-runs
    assert len(_assignments(f"keyword-kmeans:shelf:{shelf.id}")) == len(WORKS)  # replaced, not duped
    library_after = {(a.work_id, a.topic_id) for a in _assignments("keyword-kmeans:library:all")}
    assert library_after == library_before
