"""Additional non-brittle algorithm-contract tests.

These tests avoid exact cluster membership or exact floating-point expectations.
They instead check stability, idempotence, score ordering, and scope isolation.
"""

from __future__ import annotations

from app.models.ai import TopicAssignment
from app.models.organization import Rack, RackShelf, Shelf, ShelfWork
from app.models.work import Work
from app.services.topic_modeling import model_topics
from sqlalchemy import func, select


def _work(title: str, abstract: str) -> Work:
    return Work(canonical_title=title, normalized_title=title.lower(), abstract=abstract)


def test_topic_modeling_replaces_prior_assignments_for_same_scope(db) -> None:
    works = [
        _work("Graph neural networks", "graphs nodes edges message passing"),
        _work("Transformer attention", "attention heads tokens sequence language"),
        _work("Sourdough bread", "starter dough flour fermentation bake"),
        _work("Oven roasting", "oven heat roast vegetables bake"),
    ]
    shelf = Shelf(name="Mixed shelf")
    db.add(shelf)
    db.add_all(works)
    db.flush()
    for work in works:
        db.add(ShelfWork(shelf_id=shelf.id, work_id=work.id))
    db.commit()

    first = model_topics(db, scope_type="shelf", scope_id=shelf.id, max_topics=3)
    db.commit()
    first_assignment_count = db.scalar(
        select(func.count())
        .select_from(TopicAssignment)
        .where(TopicAssignment.topic_model_id == first["model_id"])
    )

    second = model_topics(db, scope_type="shelf", scope_id=shelf.id, max_topics=2)
    db.commit()
    assignments = db.scalars(
        select(TopicAssignment).where(TopicAssignment.topic_model_id == second["model_id"])
    ).all()

    assert first["model_id"] == second["model_id"]
    assert first_assignment_count == len(works)
    assert len(assignments) == len(works)
    assert {assignment.work_id for assignment in assignments} == {work.id for work in works}
    assert len({assignment.topic_id for assignment in assignments}) <= 2


def test_topic_modeling_rack_scope_deduplicates_works_across_shelves(db) -> None:
    shared = _work("Shared retrieval paper", "retrieval embedding vector search ranking")
    shelf_a = Shelf(name="A")
    shelf_b = Shelf(name="B")
    rack = Rack(name="Rack")
    db.add_all([shared, shelf_a, shelf_b, rack])
    db.flush()
    db.add_all(
        [
            ShelfWork(shelf_id=shelf_a.id, work_id=shared.id),
            ShelfWork(shelf_id=shelf_b.id, work_id=shared.id),
            RackShelf(rack_id=rack.id, shelf_id=shelf_a.id),
            RackShelf(rack_id=rack.id, shelf_id=shelf_b.id),
        ]
    )
    db.commit()

    result = model_topics(db, scope_type="rack", scope_id=rack.id, max_topics=5)

    assert result["work_count"] == 1
    assert len(result["topics"]) == 1
    assert result["topics"][0]["work_count"] == 1
