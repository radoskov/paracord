"""Future acceptance tests for local-LLM summaries.

The current summarizer is local extractive/abstract-only. This test captures the
later Ollama/llama.cpp contract while remaining disabled now.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(reason="future stage: local LLM summary provider")


def test_local_llm_summary_records_model_prompt_and_source_sections(client, auth_headers) -> None:
    headers = auth_headers("editor")
    work = client.post(
        "/api/v1/works",
        headers=headers,
        json={
            "canonical_title": "Efficient Transformers",
            "abstract": "We study efficient local attention models.",
        },
    ).json()

    summary = client.post(
        f"/api/v1/works/{work['id']}/summaries",
        headers=headers,
        json={"summary_type": "local_llm", "model_name": "qwen3:4b"},
    )

    assert summary.status_code == 201
    body = summary.json()
    assert body["summary_type"] == "local_llm"
    assert body["model_name"] == "qwen3:4b"
    assert body["prompt_version"]
    assert "source_sections" in body
