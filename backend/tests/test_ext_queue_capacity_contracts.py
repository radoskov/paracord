"""Queue-capacity guard contract tests.

The guard intentionally fails open when Redis depth cannot be measured, but rejects
job-creating requests once measured depth reaches the configured ceiling.
"""

from __future__ import annotations

import pytest
from app.services import queue_capacity
from fastapi import HTTPException


def test_queue_capacity_fails_open_when_depth_is_unavailable(db, monkeypatch) -> None:
    monkeypatch.setattr(queue_capacity.queue, "pending_queue_depth", lambda: None)
    monkeypatch.setattr(queue_capacity, "effective_max_queue_len", lambda _db: 1)

    queue_capacity.assert_queue_has_capacity(db)


def test_queue_capacity_allows_below_limit(db, monkeypatch) -> None:
    monkeypatch.setattr(queue_capacity.queue, "pending_queue_depth", lambda: 4)
    monkeypatch.setattr(queue_capacity, "effective_max_queue_len", lambda _db: 5)

    queue_capacity.assert_queue_has_capacity(db)


def test_queue_capacity_rejects_at_limit_with_actionable_status(db, monkeypatch) -> None:
    monkeypatch.setattr(queue_capacity.queue, "pending_queue_depth", lambda: 5)
    monkeypatch.setattr(queue_capacity, "effective_max_queue_len", lambda _db: 5)

    with pytest.raises(HTTPException) as excinfo:
        queue_capacity.assert_queue_has_capacity(db)

    assert excinfo.value.status_code == 429
    assert "queue is full" in str(excinfo.value.detail).lower()
