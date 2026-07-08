"""Future acceptance contracts for richer topic/GROBID/local-AI behavior.

These tests are intentionally skipped until the corresponding verticals become
stable enough to enforce. They document desired behavior without weakening the
current routine suite.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(
    reason="future acceptance contract; enable when feature is implemented"
)


def test_future_grobid_coordinates_drive_reader_overlay_acceptance():
    """A real TEI fixture should round-trip citation coordinates to reader boxes."""
    raise NotImplementedError


def test_future_local_llm_summary_records_prompt_model_and_fallback_provenance():
    """Local summaries should store provider/model/prompt and explain any fallback."""
    raise NotImplementedError


def test_future_topic_backend_can_compare_topics_across_shelves_and_racks():
    """A BERTopic/embedding backend should expose stable scope-aware topic comparison."""
    raise NotImplementedError


def test_future_agent_teleport_round_trip_preserves_hash_and_hides_local_path():
    """Agent index->server request->teleport should verify SHA and never expose raw path."""
    raise NotImplementedError
