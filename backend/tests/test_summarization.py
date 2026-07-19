"""Local summarization tests (M7, tiers 0 and 1)."""

import uuid
from pathlib import Path

import pytest
from app.db.base import Base
from app.models.ai import Summary
from app.models.citation import RawTeiDocument
from app.models.organization import Shelf, ShelfWork
from app.models.work import Work
from app.services.summarization import (
    list_work_summaries,
    summarize_extractive,
    summarize_scope,
    summarize_work,
)
from app.services.tei_parser import extract_body_text
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

TEI_BODY = """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <text><body>
    <div><head>Method</head>
      <p>The transformer relies on attention. Attention replaces recurrence with attention.</p>
    </div>
    <div><head>Results</head>
      <p>Attention improves translation quality. The weather outside is unrelated and sunny.</p>
    </div>
  </body></text>
</TEI>
"""


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'summaries.db'}")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            Work.__table__,
            Summary.__table__,
            RawTeiDocument.__table__,
            Shelf.__table__,
            ShelfWork.__table__,
        ],
    )
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with session_local() as session:
        yield session


def test_ollama_generate_streams_and_supports_midstream_cancel(monkeypatch) -> None:
    """_ollama_generate streams the response (so a cancel takes effect mid-generation): it joins the
    'response' chunks, and a cancel probe that returns True aborts with JobCancelled before finishing."""
    import app.services.summarization as summ
    import httpx2
    from app.workers.queue import JobCancelled

    lines = [
        '{"response": "Hello "}',
        '{"response": "world.", "done": true, "done_reason": "stop"}',
    ]

    class _Resp:
        status_code = 200

        def read(self):
            return None

        def raise_for_status(self):
            return None

        def iter_lines(self):
            yield from lines

    class _Stream:
        def __enter__(self):
            return _Resp()

        def __exit__(self, *_a):
            return False

    class _Client:
        def __init__(self, *_a, **_k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def stream(self, *_a, **_k):
            return _Stream()

    monkeypatch.setattr(httpx2, "Client", _Client)

    assert (
        summ._ollama_generate("p", model="m", base_url="http://x", opts=summ._LlmOpts())
        == "Hello world."
    )
    with pytest.raises(JobCancelled):
        summ._ollama_generate(
            "p", model="m", base_url="http://x", opts=summ._LlmOpts(cancel_cb=lambda: True)
        )


# --- reasoning-model output cleanup + keep_alive -----------------------------


def test_strip_reasoning_removes_think_blocks() -> None:
    from app.services.summarization import _strip_reasoning

    # A complete <think>…</think> block is removed; the answer survives.
    assert (
        _strip_reasoning("<think>Let me plan this out.</think>The paper studies X.")
        == "The paper studies X."
    )
    # Case-insensitive + multiline reasoning is stripped.
    assert _strip_reasoning("<THINK>\nline1\nline2\n</THINK>\n\nFinal answer.") == "Final answer."
    # An asymmetric block (only a closing tag survived truncation) keeps what follows it.
    assert (
        _strip_reasoning("reasoning noise... </think> The actual summary.") == "The actual summary."
    )
    # Plain text (no thinking model) is returned untouched (just trimmed).
    assert _strip_reasoning("  A normal summary.  ") == "A normal summary."


def test_keep_alive_value_pins_or_times_out() -> None:
    from app.services.ai_config import EffectiveAIConfig, keep_alive_value

    def cfg(**over):
        base = dict(
            embedding_provider="ollama",
            embedding_model="nomic-embed-text",
            summary_provider="local_llm",
            summary_model="qwen3.5:4b",
            topic_backend="tfidf",
            topic_embedding_model=None,
            ocr_backend="none",
            ocr_language="eng",
            ollama_url="http://ollama:11434",
            vram_budget_gb=None,
            query_cache_size=2048,
            auto_unmount=True,
            auto_unmount_minutes=5.0,
            summary_llm_timeout=120.0,
            summary_reasoning=False,
        )
        base.update(over)
        return EffectiveAIConfig(**base)

    # Auto-unmount on → the idle timeout in seconds; off → -1 (pin, never auto-unload).
    assert keep_alive_value(cfg(auto_unmount=True, auto_unmount_minutes=5.0)) == 300
    assert keep_alive_value(cfg(auto_unmount=True, auto_unmount_minutes=0.5)) == 30
    assert keep_alive_value(cfg(auto_unmount=False)) == -1


def test_llm_opts_reasoning_only_for_capable_models(monkeypatch) -> None:
    """think is sent ONLY for reasoning-capable models, carrying the opt-in; a plain model gets no
    think flag at all (Ollama rejects it), and the configured timeout flows through."""
    import app.services.model_management as mm
    import app.services.summarization as summ
    from app.services.ai_config import EffectiveAIConfig

    def cfg(**over):
        base = dict(
            embedding_provider="ollama",
            embedding_model="nomic-embed-text",
            summary_provider="local_llm",
            summary_model="qwen3.5:4b",
            topic_backend="tfidf",
            topic_embedding_model=None,
            ocr_backend="none",
            ocr_language="eng",
            ollama_url="http://ollama:11434",
            vram_budget_gb=None,
            query_cache_size=2048,
            auto_unmount=True,
            auto_unmount_minutes=5.0,
            summary_llm_timeout=900.0,
            summary_reasoning=True,
        )
        base.update(over)
        return EffectiveAIConfig(**base)

    monkeypatch.setattr(mm, "model_supports_thinking", lambda model, *, ollama_url: True)
    # Reasoning on + a high timeout passes through unchanged; the context is widened so the
    # chain-of-thought can't crowd out the answer (the empty-response → extractive-fallback bug).
    opts = summ._llm_opts_for(cfg(summary_reasoning=True, summary_llm_timeout=900.0), "qwen3.5:4b")
    assert opts.think is True and opts.timeout == 900.0
    assert opts.num_ctx == summ.REASONING_NUM_CTX
    # Reasoning on + a low timeout is floored so slow reasoning isn't prematurely cancelled.
    opts = summ._llm_opts_for(cfg(summary_reasoning=True, summary_llm_timeout=120.0), "qwen3.5:4b")
    assert opts.think is True and opts.timeout == summ.REASONING_MIN_TIMEOUT_S
    # Opted out → suppress thinking; the low timeout is respected as-is; no context widening.
    opts = summ._llm_opts_for(cfg(summary_reasoning=False, summary_llm_timeout=120.0), "qwen3.5:4b")
    assert opts.think is False and opts.timeout == 120.0 and opts.num_ctx is None

    # A non-reasoning model never gets the think flag, regardless of the setting.
    monkeypatch.setattr(mm, "model_supports_thinking", lambda model, *, ollama_url: False)
    opts = summ._llm_opts_for(cfg(summary_reasoning=True), "llama3")
    assert opts.think is None


def test_reasoning_summary_stored_as_distinct_model_version(db, monkeypatch) -> None:
    """A reasoning-mode summary is saved under a distinct provenance label ('<model> (reasoning)') so
    it coexists with the normal version as its own history entry — while the model actually sent to
    Ollama stays the real name."""
    import app.services.model_management as mm
    import app.services.summarization as summ
    from app.models.work import Work
    from app.services.ai_config import update_ai_config

    monkeypatch.setattr(mm, "model_supports_thinking", lambda model, *, ollama_url: True)
    sent_models: list[str] = []

    def fake_gen(prompt, *, model, base_url, opts=None):
        sent_models.append(model)
        return "An LLM summary sentence."

    monkeypatch.setattr(summ, "_ollama_generate", fake_gen)
    update_ai_config(
        db,
        changes={
            "summary_provider": "local_llm",
            "summary_model": "qwen3.5:4b",
            "summary_reasoning": True,
        },
    )
    db.commit()
    work = Work(
        canonical_title="t",
        normalized_title="t",
        abstract="A sufficiently wordy abstract to sum up.",
    )
    db.add(work)
    db.commit()

    s = summ.summarize_work(db, work, summary_type="local_llm", detail="short")
    db.commit()
    # Stored under the reasoning label, but Ollama was called with the real model name.
    assert s.model_name == "qwen3.5:4b (reasoning)"
    assert s.provider_used == "local_llm"
    assert sent_models and all(m == "qwen3.5:4b" for m in sent_models)


def test_promote_summary_makes_a_version_current(db_session) -> None:
    """Promoting a stored version makes it sort first (current) via COALESCE(promoted_at, created_at),
    without rewriting its created_at."""
    from app.services.summarization import (
        list_work_summaries,
        promote_work_summary,
        summarize_work,
    )

    work = Work(canonical_title="t", normalized_title="t", abstract="One. Two. Three. Four. Five.")
    db_session.add(work)
    db_session.commit()

    # Two short versions under distinct model names so both persist as history.
    a = summarize_work(db_session, work, summary_type="local_llm", detail="short", model_name="m-a")
    db_session.commit()
    b = summarize_work(db_session, work, summary_type="local_llm", detail="short", model_name="m-b")
    db_session.commit()
    a_id, a_created = a.id, a.created_at
    # Newest (b) is current initially.
    assert list_work_summaries(db_session, work.id)[0].id == b.id

    promoted = promote_work_summary(db_session, work.id, a_id)
    db_session.commit()
    assert promoted is not None and promoted.id == a_id
    # a is now current, and its original creation time is untouched.
    current = list_work_summaries(db_session, work.id)[0]
    assert current.id == a_id
    assert current.created_at == a_created

    # A summary that doesn't belong to the work → None (not promoted).
    assert promote_work_summary(db_session, uuid.uuid4(), a_id) is None


def test_promote_scope_summary_makes_a_version_current(db_session) -> None:
    """#22: scope summaries accumulate one row per model as history; promoting a version makes it
    the current one (leads list_scope_summaries + latest_scope_summary) without rewriting created_at."""
    from app.models.organization import Shelf, ShelfWork
    from app.services.summarization import (
        latest_scope_summary,
        list_scope_summaries,
        promote_scope_summary,
        summarize_scope,
    )

    shelf = Shelf(name="Hist")
    db_session.add(shelf)
    db_session.flush()
    work = Work(canonical_title="w", normalized_title="w", abstract="One. Two. Three. Four. Five.")
    db_session.add(work)
    db_session.flush()
    db_session.add(ShelfWork(shelf_id=shelf.id, work_id=work.id))
    db_session.commit()

    # Two scope summaries under distinct model names so both persist as history. summary_type
    # "local_llm" keeps the requested model_name as the stored label (the extractive scope path
    # collapses everything under one tier name); abstract-only works need no live LLM here.
    a, _ = summarize_scope(
        db_session, scope_type="shelf", scope_id=shelf.id, summary_type="local_llm", model_name="m-a"
    )
    db_session.commit()
    b, _ = summarize_scope(
        db_session, scope_type="shelf", scope_id=shelf.id, summary_type="local_llm", model_name="m-b"
    )
    db_session.commit()
    assert a.model_name == "m-a" and b.model_name == "m-b"
    a_id, a_created = a.id, a.created_at

    history = list_scope_summaries(db_session, scope_type="shelf", scope_id=shelf.id)
    assert {h.id for h in history} == {a.id, b.id}
    # Newest (b) is current initially.
    assert history[0].id == b.id
    assert latest_scope_summary(db_session, scope_type="shelf", scope_id=shelf.id).id == b.id

    promoted = promote_scope_summary(db_session, a_id, scope_type="shelf", scope_id=shelf.id)
    db_session.commit()
    assert promoted is not None and promoted.id == a_id
    # a is now current (leads the history + the latest read), created_at untouched.
    current = list_scope_summaries(db_session, scope_type="shelf", scope_id=shelf.id)[0]
    assert current.id == a_id
    assert current.created_at == a_created
    assert latest_scope_summary(db_session, scope_type="shelf", scope_id=shelf.id).id == a_id

    # Wrong scope / unknown id → None (not promoted).
    assert promote_scope_summary(db_session, a_id, scope_type="rack", scope_id=shelf.id) is None
    assert (
        promote_scope_summary(db_session, uuid.uuid4(), scope_type="shelf", scope_id=shelf.id)
        is None
    )


def test_short_summary_map_reduces_a_long_paper(monkeypatch) -> None:
    """A paper longer than the per-call budget is summarized in chunks (map-reduce) — several LLM
    calls, never a single truncated call — so long papers are covered and never overflow the context."""
    import app.services.summarization as summ

    calls: list[str] = []

    def fake_gen(prompt, *, model, base_url, opts=None):
        calls.append(prompt[:24])
        return "A concise digest sentence."

    monkeypatch.setattr(summ, "_ollama_generate", fake_gen)

    short = "Transformers use attention. It replaces recurrence."
    assert len(short) <= summ.LLM_INPUT_CHAR_BUDGET
    summ._short_summary_llm(short, model="m", base_url="u")
    assert len(calls) == 1  # fits the budget → a single call, no map-reduce

    calls.clear()
    long_text = "Attention replaces recurrence in the transformer model. " * 800
    assert len(long_text) > summ.LLM_INPUT_CHAR_BUDGET
    out = summ._short_summary_llm(long_text, model="m", base_url="u")
    assert out == "A concise digest sentence."
    assert len(calls) >= 2  # chunked: per-chunk maps + a final condense


# --- pure extractive summarizer ---------------------------------------------


def test_summarize_extractive_returns_short_text_unchanged() -> None:
    assert summarize_extractive("Only one sentence here.", max_sentences=5) == (
        "Only one sentence here."
    )


def test_summarize_extractive_selects_salient_sentences() -> None:
    text = (
        "The transformer uses attention. "
        "Attention over attention drives the transformer's attention layers. "
        "Cats are fluffy and sleep all day. "
        "The transformer's attention mechanism beats recurrence on attention tasks. "
        "Yesterday the weather was rainy and cold."
    )
    summary = summarize_extractive(text, max_sentences=2)
    assert summary.count(".") == 2  # exactly two sentences kept
    assert "attention" in summary.lower()
    assert "fluffy" not in summary.lower()  # off-topic sentence excluded
    assert "weather" not in summary.lower()


def test_extract_body_text_reads_tei_paragraphs() -> None:
    body = extract_body_text(TEI_BODY)
    assert body is not None
    assert "attention replaces recurrence" in body.lower()
    assert "weather outside" in body.lower()


# --- summarize_work ---------------------------------------------------------


def test_summarize_work_abstract_tier_stores_verbatim(db_session) -> None:
    work = Work(canonical_title="t", normalized_title="t", abstract="A concise abstract.")
    db_session.add(work)
    db_session.commit()

    summary = summarize_work(db_session, work, summary_type="abstract")
    db_session.commit()

    assert summary.text == "A concise abstract."
    assert summary.model_name == "tier0-abstract"
    assert summary.prompt_version == "v1"
    assert summary.entity_type == "work"


def test_summarize_work_extractive_uses_abstract_and_tei_body(db_session) -> None:
    work = Work(canonical_title="t", normalized_title="t", abstract="Short framing abstract.")
    db_session.add(work)
    db_session.flush()
    db_session.add(RawTeiDocument(file_id=uuid.uuid4(), work_id=work.id, tei_xml=TEI_BODY))
    db_session.commit()

    summary = summarize_work(db_session, work, summary_type="extractive", max_sentences=2)
    db_session.commit()

    assert summary.model_name == "tier1-extractive-frequency"
    assert "attention" in summary.text.lower()  # pulled from the TEI body


def test_summarize_work_is_idempotent_per_type(db_session) -> None:
    work = Work(
        canonical_title="t", normalized_title="t", abstract="One. Two. Three. Four. Five. Six."
    )
    db_session.add(work)
    db_session.commit()

    summarize_work(db_session, work, summary_type="extractive")
    db_session.commit()
    summarize_work(db_session, work, summary_type="extractive")
    db_session.commit()

    count = db_session.scalar(
        select(func.count()).select_from(Summary).where(Summary.summary_type == "extractive")
    )
    assert count == 1
    assert len(list_work_summaries(db_session, work.id)) == 1


def test_summarize_work_local_llm_falls_back_to_extractive_with_provenance(db_session) -> None:
    """When local_llm is requested but not enabled, the summary degrades to extractive and records
    the fallback provenance (Phase B2) so the API/UI can surface 'summarized with the fallback'."""
    work = Work(
        canonical_title="t",
        normalized_title="t",
        abstract="One. Two. Three. Four. Five. Six. Seven.",
    )
    db_session.add(work)
    db_session.commit()

    summary = summarize_work(db_session, work, summary_type="local_llm")
    db_session.commit()

    assert summary.provider_requested == "local_llm"
    assert summary.provider_used == "extractive"
    assert summary.fallback is True
    assert summary.fallback_reason  # a short human reason is present
    assert "extractive-fallback" in summary.source_sections


def test_summarize_work_extractive_is_not_marked_degraded(db_session) -> None:
    work = Work(canonical_title="t", normalized_title="t", abstract="A concise abstract.")
    db_session.add(work)
    db_session.commit()
    summary = summarize_work(db_session, work, summary_type="extractive")
    db_session.commit()
    assert summary.fallback is False
    assert summary.provider_used == summary.provider_requested == "extractive"


def test_short_and_detailed_summaries_coexist_and_use_full_body(db, monkeypatch) -> None:
    """UX batch 4: short + detailed are stored as separate rows; detailed chunks the WHOLE body
    (no 12k clip) into section paragraphs + a synthesized intro."""
    import app.services.summarization as summ
    from app.services.ai_config import update_ai_config

    db_session = db
    update_ai_config(db_session, changes={"summary_provider": "local_llm", "summary_model": "m1"})
    db_session.commit()
    seen: list[str] = []

    def fake_generate(prompt, *, model, base_url, opts=None):
        seen.append(prompt)
        if prompt.startswith(summ._DETAIL_INTRO_PROMPT[:30]):
            return "HIGH-LEVEL INTRO."
        if prompt.startswith("Summarize the"):  # section chunk prompt (carries the section name)
            return "Content summary of the section."
        return "SHORT SUMMARY."

    monkeypatch.setattr(summ, "_ollama_generate", fake_generate)

    divs = "".join(
        f"<div><head>Section {i}</head><p>{('Sentence number ' + str(i) + ' with several words. ') * 6}</p></div>"
        for i in range(3)
    )
    tei = '<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body>' + divs + "</body></text></TEI>"
    work = Work(canonical_title="t", normalized_title="t", abstract="Abstract sentence.")
    db_session.add(work)
    db_session.flush()
    db_session.add(
        RawTeiDocument(file_id=uuid.uuid4(), work_id=work.id, source="grobid", tei_xml=tei)
    )
    db_session.commit()

    short = summarize_work(db_session, work, summary_type="local_llm", detail="short")
    detailed = summarize_work(db_session, work, summary_type="local_llm", detail="detailed")
    db_session.commit()

    assert short.summary_type == "local_llm"
    # 'detailed' is the back-compat alias for the deep effort level (2026-07-16).
    assert detailed.summary_type == "local_llm_detailed_deep"
    assert short.provider_used == "local_llm" and detailed.provider_used == "local_llm"
    # Both rows persist (coexist) — the paper view shows both.
    rows = {
        s.summary_type
        for s in db_session.scalars(
            select(Summary).where(Summary.entity_type == "work", Summary.entity_id == work.id)
        ).all()
    }
    assert rows == {"local_llm", "local_llm_detailed_deep"}
    # Detailed = intro + one paragraph PER SECTION, each headed by the section name.
    assert detailed.text.startswith("HIGH-LEVEL INTRO.")
    assert "Section 0:" in detailed.text
    assert "Section 2:" in detailed.text
    assert detailed.params["detail"] == "detailed_deep"
    # The section name is passed into the chunk prompt (so the model knows which section).
    assert any('"Section 1" section' in p for p in seen)


def test_detailed_summary_reports_progress_and_honors_cancel(db, monkeypatch) -> None:
    """2026-07-16: the detailed per-paper summary reports per-section progress and stops
    cooperatively when the Jobs-tab Stop flag is set (JobCancelled propagates, not swallowed)."""
    import app.services.summarization as summ
    from app.services.ai_config import update_ai_config
    from app.workers.queue import JobCancelled

    update_ai_config(db, changes={"summary_provider": "local_llm", "summary_model": "m1"})
    db.commit()
    monkeypatch.setattr(summ, "_ollama_generate", lambda *a, **k: "para.")

    divs = "".join(f"<div><head>Section {i}</head><p>{('word ' * 8)}</p></div>" for i in range(3))
    tei = '<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body>' + divs + "</body></text></TEI>"
    work = Work(canonical_title="t", normalized_title="t", abstract="Abstract sentence.")
    db.add(work)
    db.flush()
    db.add(RawTeiDocument(file_id=uuid.uuid4(), work_id=work.id, source="grobid", tei_xml=tei))
    db.commit()

    # Progress is reported (done, total) with total > sections (the +1 intro step) and reaches full.
    calls: list[tuple[int, int]] = []
    summarize_work(
        db,
        work,
        summary_type="local_llm",
        detail="detailed",
        progress_cb=lambda done, total: calls.append((done, total)),
    )
    db.commit()
    assert calls and calls[-1][0] == calls[-1][1]  # ends at done == total
    assert calls[-1][1] >= 3  # at least the 3 sections were counted

    # A cancel flag mid-run raises JobCancelled out of summarize_work (no silent extractive fallback).
    with pytest.raises(JobCancelled):
        summarize_work(
            db,
            work,
            summary_type="local_llm",
            detail="detailed",
            cancel_cb=lambda: True,
        )


def test_title_only_work_cannot_be_summarized_locally(client, auth_headers, db) -> None:
    """2026-07-16 no-PDF honesty: a title-only paper is refused (clear 400 / ValueError), not
    silently 'summarized' from its title."""
    from app.services.ai_config import update_ai_config
    from app.services.summarization import summarize_work

    update_ai_config(db, changes={"summary_provider": "local_llm", "summary_model": "m1"})
    work = Work(canonical_title="Bare title", normalized_title="bare title")  # no abstract, no TEI
    db.add(work)
    db.commit()

    with pytest.raises(ValueError, match="only a title"):
        summarize_work(db, work, summary_type="local_llm")

    r = client.post(
        f"/api/v1/works/{work.id}/summaries",
        headers=auth_headers("editor"),
        json={"summary_type": "local_llm", "detail": "detailed"},
    )
    assert r.status_code == 400
    assert "only a title" in r.json()["detail"]


def test_abstract_only_work_is_framed_as_abstract(db, monkeypatch) -> None:
    """An abstract-only paper is summarized, and the prompt tells the model it has only the
    abstract so the summary is framed correctly."""
    import app.services.summarization as summ
    from app.services.ai_config import update_ai_config
    from app.services.summarization import summarize_work

    update_ai_config(db, changes={"summary_provider": "local_llm", "summary_model": "m1"})
    db.commit()
    seen: list[str] = []
    monkeypatch.setattr(
        summ, "_ollama_generate", lambda p, **k: seen.append(p) or "Framed summary."
    )

    work = Work(canonical_title="A", normalized_title="a", abstract="We study X and find Y.")
    db.add(work)
    db.commit()
    s = summarize_work(db, work, summary_type="local_llm")
    assert s.text == "Framed summary."
    assert any("only the ABSTRACT" in p for p in seen)


def test_scope_summary_groups_no_pdf_papers_and_reports_breakdown(db) -> None:
    """A scope with mixed sources: full-text papers are summarized; abstract-only and title-only
    papers are each folded into one paragraph, and the breakdown is recorded for the footer."""
    from app.models.organization import Shelf, ShelfWork
    from app.services.summarization import summarize_scope

    shelf = Shelf(name="mixed")
    db.add(shelf)
    db.flush()

    full = Work(canonical_title="Full", normalized_title="full", abstract="Full abstract sentence.")
    abs_only = Work(
        canonical_title="AbsOnly", normalized_title="absonly", abstract="Only an abstract here."
    )
    title_only = Work(canonical_title="TitleOnly", normalized_title="titleonly")
    for w in (full, abs_only, title_only):
        db.add(w)
        db.flush()
        db.add(ShelfWork(shelf_id=shelf.id, work_id=w.id))
    tei = (
        '<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body>'
        "<div><head>Intro</head><p>Body sentence one. Body sentence two.</p></div>"
        "</body></text></TEI>"
    )
    db.add(RawTeiDocument(file_id=uuid.uuid4(), work_id=full.id, source="grobid", tei_xml=tei))
    db.commit()

    summary, count = summarize_scope(
        db, scope_type="shelf", scope_id=shelf.id, summary_type="local_llm"
    )
    assert count == 3
    assert summary.params["source_breakdown"] == {
        "full_text": 1,
        "abstract_only": 1,
        "title_only": 1,
    }
    # The title-only paper is named in a grouped paragraph, not silently dropped.
    assert "title only" in summary.text and "TitleOnly" in summary.text
    assert "only as abstracts" in summary.text


def test_detailed_effort_levels_coexist_and_fast_categorizes(db, monkeypatch) -> None:
    """2026-07-16: fast/section/deep are stored as separate rows; fast folds sections into buckets
    via the categorizer, so it makes far fewer chunk calls than deep."""
    import app.services.summarization as summ
    from app.services.ai_config import update_ai_config

    update_ai_config(db, changes={"summary_provider": "local_llm", "summary_model": "m1"})
    db.commit()
    calls: list[str] = []

    def fake_generate(prompt, *, model, base_url, opts=None):
        calls.append(prompt)
        if prompt.startswith("Classify each academic-paper section"):
            # Categorize: sections 1..N -> Background/Methods/Results round-robin-ish.
            lines = [ln for ln in prompt.splitlines() if ln[:1].isdigit()]
            cats = ["Background", "Methods", "Results", "Methods"]
            return "\n".join(f"{i + 1}: {cats[i % len(cats)]}" for i in range(len(lines)))
        return "para."

    monkeypatch.setattr(summ, "_ollama_generate", fake_generate)
    divs = "".join(f"<div><head>Sec {i}</head><p>{'word ' * 8}</p></div>" for i in range(6))
    tei = '<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body>' + divs + "</body></text></TEI>"
    work = Work(canonical_title="t", normalized_title="t", abstract="Abstract sentence here.")
    db.add(work)
    db.flush()
    db.add(RawTeiDocument(file_id=uuid.uuid4(), work_id=work.id, source="grobid", tei_xml=tei))
    db.commit()

    for detail in ("detailed_fast", "detailed_section", "detailed_deep"):
        summarize_work(db, work, summary_type="local_llm", detail=detail)
    db.commit()

    rows = {
        s.summary_type
        for s in db.scalars(
            select(Summary).where(Summary.entity_type == "work", Summary.entity_id == work.id)
        ).all()
    }
    assert {
        "local_llm_detailed_fast",
        "local_llm_detailed_section",
        "local_llm_detailed_deep",
    } <= rows
    # Fast asked the categorizer exactly once.
    assert sum(1 for c in calls if c.startswith("Classify each academic-paper section")) == 1


def test_summary_cache_keeps_multiple_models_and_evicts_lru(db, monkeypatch) -> None:
    """The cache matrix keeps one row per (detail, model), up to SUMMARY_MODEL_CACHE models (LRU)."""
    import app.services.summarization as summ
    from app.services.ai_config import update_ai_config

    update_ai_config(db, changes={"summary_provider": "local_llm", "summary_model": "m1"})
    db.commit()
    monkeypatch.setattr(summ, "_ollama_generate", lambda *a, **k: "para.")
    monkeypatch.setattr(summ, "SUMMARY_MODEL_CACHE", 3)

    work = Work(canonical_title="t", normalized_title="t", abstract="One. Two. Three.")
    db.add(work)
    db.commit()

    for m in ("mA", "mB", "mC", "mD"):  # 4 models, cap 3 → oldest (mA) evicted
        summarize_work(db, work, summary_type="local_llm", detail="short", model_name=m)
    db.commit()

    models = [
        s.model_name
        for s in db.scalars(
            select(Summary).where(
                Summary.entity_type == "work",
                Summary.entity_id == work.id,
                Summary.summary_type == "local_llm",
            )
        ).all()
    ]
    assert set(models) == {"mB", "mC", "mD"}  # mA evicted; a different detail level is unaffected


def test_scope_reuse_refreshes_a_paper_that_gained_a_pdf(db, monkeypatch) -> None:
    """2026-07-16: a scope re-run WITHOUT regenerate_papers reuses existing per-paper summaries, but
    a paper that has since gained full text (was abstract-only) is re-summarized (not left stale)."""
    import app.services.summarization as summ
    from app.models.organization import Shelf, ShelfWork
    from app.services.ai_config import update_ai_config
    from app.services.summarization import summarize_scope, summarize_work

    update_ai_config(db, changes={"summary_provider": "local_llm", "summary_model": "m1"})
    db.commit()
    monkeypatch.setattr(summ, "_ollama_generate", lambda *a, **k: "digest.")

    shelf = Shelf(name="grow")
    db.add(shelf)
    db.flush()
    work = Work(canonical_title="P", normalized_title="p", abstract="An abstract only.")
    db.add(work)
    db.flush()
    db.add(ShelfWork(shelf_id=shelf.id, work_id=work.id))
    db.commit()

    # The paper was summarized in its own view while abstract-only (marked abstract-only).
    summarize_work(db, work, summary_type="local_llm", detail="short", model_name="m1")
    db.commit()
    first = db.scalars(
        select(Summary).where(
            Summary.entity_type == "work",
            Summary.entity_id == work.id,
            Summary.summary_type == "local_llm",
        )
    ).first()
    assert first is not None and "abstract-only" in (first.source_sections or [])

    # The paper gains a PDF (GROBID body); re-run the scope WITHOUT regenerate_papers.
    tei = (
        '<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body>'
        "<div><head>Intro</head><p>Now there is full body text here.</p></div>"
        "</body></text></TEI>"
    )
    db.add(RawTeiDocument(file_id=uuid.uuid4(), work_id=work.id, source="grobid", tei_xml=tei))
    db.commit()
    summarize_scope(db, scope_type="shelf", scope_id=shelf.id, summary_type="local_llm")
    db.commit()

    refreshed = db.scalars(
        select(Summary).where(
            Summary.entity_type == "work",
            Summary.entity_id == work.id,
            Summary.summary_type == "local_llm",
        )
    ).first()
    # The stale abstract-only summary was replaced by a full-text one.
    assert refreshed is not None and "abstract-only" not in (refreshed.source_sections or [])


def test_coalesce_main_sections_folds_numbered_subsections() -> None:
    """2026-07-16: the 'section' level groups by MAIN section — numbered subsections fold into their
    parent (13 main sections, not 20 main+sub)."""
    from app.services.summarization import _coalesce_main_sections

    secs = [
        ("1 Introduction", "intro."),
        ("1.1 Background", "bg."),
        ("2 Methods", "meth."),
        ("2.1 Setup", "setup."),
        ("2.2 Data", "data."),
        ("3 Results", "res."),
    ]
    mains = _coalesce_main_sections(secs)
    assert [lbl for lbl, _ in mains] == ["1 Introduction", "2 Methods", "3 Results"]
    methods_text = dict(mains)["2 Methods"]
    assert "meth." in methods_text and "setup." in methods_text and "data." in methods_text


def test_coalesce_main_sections_roman_scheme_with_letter_subsections() -> None:
    """Roman-numeral main sections (I/II/III…) with capital-letter subsections (A/B/C…), incl. the
    C/D letters that are ALSO valid roman numerals — the sequence check keeps them as subsections.
    A pre-first-main lead-in folds into the first main (so the count matches the real paper)."""
    from app.services.summarization import _coalesce_main_sections

    secs = [
        ("Abstract lead-in with no head", "lead."),
        ("I. INTRODUCTION", "intro."),
        ("II. ROLE", "role."),
        ("A. Bridging", "a."),
        ("B. Prior", "b."),
        ("C. Representing", "c."),  # C is roman 100 — must NOT be seen as a main
        ("III. MINING", "mining."),
        ("D. Clustering", "d."),  # D is roman 500 — must NOT be seen as a main
        ("IV. EVALUATION", "eval."),
        ("V. OTHERS", "others."),
        ("VI. CONCLUSION", "concl."),
    ]
    mains = _coalesce_main_sections(secs)
    assert [lbl for lbl, _ in mains] == [
        "I. INTRODUCTION",
        "II. ROLE",
        "III. MINING",
        "IV. EVALUATION",
        "V. OTHERS",
        "VI. CONCLUSION",
    ]
    # Subsection text folds into its parent main; the lead-in folds into the first main.
    assert "lead." in dict(mains)["I. INTRODUCTION"]
    assert "a." in dict(mains)["II. ROLE"] and "c." in dict(mains)["II. ROLE"]


def test_extract_sections_prefixes_the_head_number_attribute() -> None:
    """GROBID often carries the section number in ``head @n`` with a bare title; extract_sections
    prefixes it so main/sub detection sees "1. Introduction" / "2.1. Design" (2026-07-16)."""
    from app.services.tei_parser import extract_sections

    tei = (
        '<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body>'
        '<div><head n="1.">Introduction</head><p>intro body.</p></div>'
        '<div><head n="2.">Methods</head><p>methods body.</p></div>'
        '<div><head n="2.1.">Design</head><p>design body.</p></div>'
        "</body></text></TEI>"
    )
    labels = [lbl for lbl, _ in extract_sections(tei)]
    assert labels == ["1. Introduction", "2. Methods", "2.1. Design"]


def test_summaries_exclude_funding_and_acknowledgements(db) -> None:
    """Funding / Acknowledgements never feed the summary source (2026-07-16)."""
    from app.services.summarization import _work_source

    divs = (
        "<div><head>Methods</head><p>The attention mechanism drives the model.</p></div>"
        "<div><head>Funding</head><p>Supported by grant number 12345.</p></div>"
        "<div><head>Acknowledgements</head><p>We thank our reviewers.</p></div>"
    )
    tei = '<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body>' + divs + "</body></text></TEI>"
    work = Work(canonical_title="t", normalized_title="t", abstract="Abstract.")
    db.add(work)
    db.flush()
    db.add(RawTeiDocument(file_id=uuid.uuid4(), work_id=work.id, source="grobid", tei_xml=tei))
    db.commit()

    text, labels = _work_source(db, work)
    assert "attention mechanism" in text.lower()  # Methods kept
    assert "grant number" not in text.lower()  # Funding excluded
    assert "we thank" not in text.lower()  # Acknowledgements excluded
    assert "body" in labels


def test_scope_summary_keeps_requested_model_when_it_degrades(db, monkeypatch) -> None:
    """2026-07-16: a scope summary that degrades to extractive keeps the REQUESTED model name (so the
    effort×model read still finds it) while recording provider_used=extractive."""
    import app.services.summarization as summ
    from app.models.organization import Shelf, ShelfWork
    from app.services.ai_config import update_ai_config
    from app.services.summarization import latest_scope_summary, summarize_scope

    update_ai_config(db, changes={"summary_provider": "local_llm", "summary_model": "m1"})
    db.commit()

    def boom(*a, **k):
        raise RuntimeError("llm down")

    monkeypatch.setattr(summ, "_ollama_generate", boom)

    shelf = Shelf(name="deg")
    db.add(shelf)
    db.flush()
    work = Work(canonical_title="P", normalized_title="p", abstract="Sentence one. Sentence two.")
    db.add(work)
    db.flush()
    db.add(ShelfWork(shelf_id=shelf.id, work_id=work.id))
    tei = (
        '<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body>'
        "<div><head>Intro</head><p>Body text one. Body text two.</p></div>"
        "</body></text></TEI>"
    )
    db.add(RawTeiDocument(file_id=uuid.uuid4(), work_id=work.id, source="grobid", tei_xml=tei))
    db.commit()

    summary, _ = summarize_scope(
        db, scope_type="shelf", scope_id=shelf.id, summary_type="local_llm"
    )
    db.commit()
    assert summary.provider_used == "extractive"  # degraded honestly
    assert summary.model_name == "m1"  # but stored under the requested model, not a tier1 name
    # The effort×model read finds it (the bug was a 404 → empty window).
    found = latest_scope_summary(
        db, scope_type="shelf", scope_id=shelf.id, summary_type="local_llm", model_name="m1"
    )
    assert found is not None and found.text


def test_summarize_work_rejects_unknown_type(db_session) -> None:
    work = Work(canonical_title="t", normalized_title="t", abstract="x")
    db_session.add(work)
    db_session.commit()
    with pytest.raises(ValueError, match="Unsupported summary type"):
        summarize_work(db_session, work, summary_type="abstractive")


def test_summarize_work_without_text_raises(db_session) -> None:
    work = Work(canonical_title="t", normalized_title="t")  # no abstract, no TEI
    db_session.add(work)
    db_session.commit()
    with pytest.raises(ValueError, match="No text available"):
        summarize_work(db_session, work, summary_type="extractive")


def test_summarize_work_persists_provenance_columns(db_session) -> None:
    """D31.2 — provenance is stored on the row, not just returned transiently."""
    work = Work(canonical_title="t", normalized_title="t", abstract="One. Two. Three. Four.")
    db_session.add(work)
    db_session.commit()

    actor_id = uuid.uuid4()
    summarize_work(db_session, work, summary_type="extractive", created_by_user_id=actor_id)
    db_session.commit()

    # Reload from the DB (expire everything) so we read persisted columns, not in-memory attrs.
    db_session.expire_all()
    stored = db_session.scalars(select(Summary).where(Summary.entity_id == work.id)).one()
    assert stored.provider_requested == "extractive"
    assert stored.provider_used == "extractive"
    assert stored.fallback is False
    assert stored.source_sections == []
    assert stored.content_hash and len(stored.content_hash) == 64
    assert stored.created_by_user_id == actor_id
    assert stored.params["summary_type"] == "extractive"


def test_summarize_scope_persists_provenance_columns(db_session) -> None:
    shelf = Shelf(name="Prov scope")
    db_session.add(shelf)
    db_session.flush()
    work = Work(canonical_title="w", normalized_title="w", abstract="Alpha. Beta. Gamma. Delta.")
    db_session.add(work)
    db_session.flush()
    db_session.add(ShelfWork(shelf_id=shelf.id, work_id=work.id))
    db_session.commit()

    actor_id = uuid.uuid4()
    summarize_scope(db_session, scope_type="shelf", scope_id=shelf.id, created_by_user_id=actor_id)
    db_session.commit()

    db_session.expire_all()
    stored = db_session.scalars(select(Summary).where(Summary.entity_type == "shelf")).one()
    assert stored.content_hash and len(stored.content_hash) == 64
    assert stored.created_by_user_id == actor_id
    assert stored.params["scope_type"] == "shelf"


# --- API surface ------------------------------------------------------------


def test_summary_api_rejects_unsupported_type(client, auth_headers, db) -> None:
    work = Work(canonical_title="t", normalized_title="t", abstract="x")
    db.add(work)
    db.commit()
    r = client.post(
        f"/api/v1/works/{work.id}/summaries",
        headers=auth_headers("editor"),
        json={"summary_type": "telepathic"},
    )
    assert r.status_code == 400


def test_summary_api_returns_provenance(client, auth_headers, db) -> None:
    work = Work(canonical_title="t", normalized_title="t", abstract="A real abstract sentence.")
    db.add(work)
    db.commit()
    created = client.post(
        f"/api/v1/works/{work.id}/summaries",
        headers=auth_headers("editor"),
        json={"summary_type": "abstract"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["model_name"] == "tier0-abstract"
    assert body["prompt_version"] == "v1"
    assert body["text"] == "A real abstract sentence."
    # D31.2 provenance surfaced in the read schema.
    assert body["content_hash"] and len(body["content_hash"]) == 64
    assert body["created_by_user_id"]
    assert body["params"]["summary_type"] == "abstract"


def test_summary_api_auto_uses_configured_provider(client, auth_headers, db) -> None:
    """B8: summary_type='auto' resolves to the configured provider. With no local LLM enabled
    (the test default), that is the deterministic extractive engine — the paper-view Summarise
    action never needs to know the AI config."""
    work = Work(
        canonical_title="t",
        normalized_title="t",
        abstract="One. Two. Three. Four. Five. Six.",
    )
    db.add(work)
    db.commit()
    created = client.post(
        f"/api/v1/works/{work.id}/summaries",
        headers=auth_headers("editor"),
        json={"summary_type": "auto"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["summary_type"] == "extractive"  # resolved from 'auto'
    assert body["text"]


def test_detailed_summary_api_runs_as_a_job(client, auth_headers, db, monkeypatch) -> None:
    """UX batch 4b: a detailed local_llm summary is enqueued (202 + job id) instead of blocking
    the request, since its section-by-section passes can take minutes."""
    from app.services.ai_config import update_ai_config

    update_ai_config(db, changes={"summary_provider": "local_llm", "summary_model": "m1"})
    db.commit()

    # Pin the enqueue to a deterministic id: the unit suite runs without Redis, so a live
    # enqueue_work_summary returns None (queue down) and the endpoint would fall through to the
    # inline 201 path. Whether the dev stack's Redis happens to be reachable otherwise makes this
    # 202-vs-201 flaky — green locally, red on CI. Stub it to the queued path we mean to assert.
    monkeypatch.setattr(
        "app.api.v1.endpoints.works.enqueue_work_summary",
        lambda work_id, detail="short": f"summarize-{work_id}-{detail}",
    )

    work = Work(canonical_title="t", normalized_title="t", abstract="One. Two. Three.")
    db.add(work)
    db.commit()
    r = client.post(
        f"/api/v1/works/{work.id}/summaries",
        headers=auth_headers("editor"),
        json={"summary_type": "auto", "detail": "detailed"},
    )
    assert r.status_code == 202
    body = r.json()
    assert body["queued"] is True
    # A real detailed summary job was enqueued (coalescing key names the work + the detailed variant).
    assert body["job_id"].startswith("summarize-") and "detailed" in body["job_id"]
    assert body["summary_type"] == "local_llm_detailed_deep"


def test_detailed_summary_enqueues_a_real_rq_job(client, auth_headers, db, requires_redis) -> None:
    """Integration: with a real Redis/RQ present, the detailed-summary endpoint actually enqueues a
    job on the queue (not just the stubbed branch the unit test above asserts).

    Runs on a dev box whose docker stack includes Redis; self-skips on the Redis-less CI backend
    job (see the ``requires_redis`` fixture). This is the test that proves the queue plumbing —
    endpoint -> enqueue_work_summary -> get_queue().enqueue -> a fetchable RQ job — works end to end.
    """
    from app.services.ai_config import update_ai_config
    from app.workers.queue import SUMMARIZE_WORK_JOB, get_queue

    update_ai_config(db, changes={"summary_provider": "local_llm", "summary_model": "m1"})
    db.commit()

    work = Work(canonical_title="t", normalized_title="t", abstract="One. Two. Three.")
    db.add(work)
    db.commit()
    work_id = work.id

    r = client.post(
        f"/api/v1/works/{work_id}/summaries",
        headers=auth_headers("editor"),
        json={"summary_type": "auto", "detail": "detailed"},
    )
    assert r.status_code == 202
    job_id = r.json()["job_id"]

    # Fetch the job straight back out of Redis to prove it was really registered, then delete it so
    # a live dev worker never runs a summary for this rolled-back test work.
    from rq.job import Job

    queue = get_queue()
    job = Job.fetch(job_id, connection=queue.connection)
    try:
        assert job.func_name == SUMMARIZE_WORK_JOB
        assert job.args[0] == str(work_id)
        assert job.args[1] == "detailed"
    finally:
        job.delete()


def test_summary_api_surfaces_extractive_fallback(client, auth_headers, db) -> None:
    """A local_llm summary that degraded to extractive reports the fallback in the response so the
    paper view can show 'Summarized with the extractive fallback (LLM unavailable)' (Phase B2)."""
    work = Work(
        canonical_title="t",
        normalized_title="t",
        abstract="One. Two. Three. Four. Five. Six. Seven.",
    )
    db.add(work)
    db.commit()
    created = client.post(
        f"/api/v1/works/{work.id}/summaries",
        headers=auth_headers("editor"),
        json={"summary_type": "local_llm"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["provider_requested"] == "local_llm"
    assert body["provider_used"] == "extractive"
    assert body["fallback"] is True
    assert body["fallback_reason"]


def test_local_llm_summary_records_model_prompt_and_source_sections(client, auth_headers) -> None:
    """The opt-in local-LLM provider degrades to extractive when Ollama is unavailable (as in CI)
    while still echoing the requested model, prompt version, and the source sections it fed on."""
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


# --- scope-level summaries --------------------------------------------------


def test_summarize_scope_shelf(db_session) -> None:
    shelf = Shelf(name="ML")
    db_session.add(shelf)
    db_session.flush()
    for i in range(3):
        work = Work(
            canonical_title=f"Paper {i}",
            normalized_title=f"paper {i}",
            abstract=f"Abstract sentence {i}. It contains technical content about neural networks.",
        )
        db_session.add(work)
        db_session.flush()
        db_session.add(ShelfWork(shelf_id=shelf.id, work_id=work.id))
    db_session.commit()

    summary, count = summarize_scope(db_session, scope_type="shelf", scope_id=shelf.id)
    db_session.commit()

    assert count == 3
    assert summary.entity_type == "shelf"
    assert summary.entity_id == shelf.id
    assert summary.model_name == "tier1-extractive-frequency-scope"
    assert len(summary.text) > 10


def test_summarize_scope_is_idempotent(db_session) -> None:
    shelf = Shelf(name="Idm")
    db_session.add(shelf)
    db_session.flush()
    work = Work(canonical_title="w", normalized_title="w", abstract="A short abstract here.")
    db_session.add(work)
    db_session.flush()
    db_session.add(ShelfWork(shelf_id=shelf.id, work_id=work.id))
    db_session.commit()

    summarize_scope(db_session, scope_type="shelf", scope_id=shelf.id)
    db_session.commit()
    summarize_scope(db_session, scope_type="shelf", scope_id=shelf.id)
    db_session.commit()

    count = db_session.scalar(
        select(func.count()).select_from(Summary).where(Summary.entity_type == "shelf")
    )
    assert count == 1


def test_summarize_scope_raises_when_no_abstracts(db_session) -> None:
    shelf = Shelf(name="Empty")
    db_session.add(shelf)
    db_session.commit()
    with pytest.raises(ValueError, match="No abstracts"):
        summarize_scope(db_session, scope_type="shelf", scope_id=shelf.id)


def test_scope_summary_api_creates_and_returns(client, auth_headers, db) -> None:
    from app.models.organization import Shelf, ShelfWork

    shelf = Shelf(name="Scope test")
    db.add(shelf)
    db.flush()
    for i in range(2):
        work = Work(
            canonical_title=f"W{i}",
            normalized_title=f"w{i}",
            abstract=f"The paper presents research on topic {i}. Experiments show improvements.",
        )
        db.add(work)
        db.flush()
        db.add(ShelfWork(shelf_id=shelf.id, work_id=work.id))
    db.commit()

    r = client.post(
        "/api/v1/ai/summaries",
        headers=auth_headers("editor"),
        json={"scope_type": "shelf", "scope_id": str(shelf.id)},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["entity_type"] == "shelf"
    assert body["work_count"] == 2
    assert body["model_name"] == "tier1-extractive-frequency-scope"
    assert len(body["text"]) > 10


def test_scope_summary_history_and_promote_api(client, auth_headers, db) -> None:
    """#22: GET /ai/summaries/history lists a scope's stored versions current-first, and POST
    /ai/summaries/{id}/promote makes one the current version — reflected in GET .../latest."""
    from app.models.organization import Shelf, ShelfWork

    shelf = Shelf(name="Hist API")
    db.add(shelf)
    db.flush()
    work = Work(canonical_title="w", normalized_title="w", abstract="One. Two. Three. Four.")
    db.add(work)
    db.flush()
    db.add(ShelfWork(shelf_id=shelf.id, work_id=work.id))
    db.commit()

    # Seed two versions under distinct model names (local_llm keeps the requested model as the label).
    a, _ = summarize_scope(
        db, scope_type="shelf", scope_id=shelf.id, summary_type="local_llm", model_name="m-a"
    )
    b, _ = summarize_scope(
        db, scope_type="shelf", scope_id=shelf.id, summary_type="local_llm", model_name="m-b"
    )
    db.commit()
    a_id = str(a.id)

    hist = client.get(
        f"/api/v1/ai/summaries/history?scope_type=shelf&scope_id={shelf.id}",
        headers=auth_headers("editor"),
    )
    assert hist.status_code == 200
    rows = hist.json()
    assert {r["id"] for r in rows} == {str(a.id), str(b.id)}
    assert rows[0]["id"] == str(b.id)  # newest is current initially
    assert rows[0]["promoted_at"] is None

    promote = client.post(
        f"/api/v1/ai/summaries/{a_id}/promote",
        headers=auth_headers("editor"),
        json={"scope_type": "shelf", "scope_id": str(shelf.id)},
    )
    assert promote.status_code == 200
    assert promote.json()["id"] == a_id
    assert promote.json()["promoted_at"] is not None

    latest = client.get(
        f"/api/v1/ai/summaries/latest?scope_type=shelf&scope_id={shelf.id}"
        "&detail=short&summary_type=local_llm",
        headers=auth_headers("editor"),
    )
    assert latest.status_code == 200
    assert latest.json()["id"] == a_id  # promoted version now leads the read


def test_scope_summary_api_empty_scope_returns_400(client, auth_headers, db) -> None:
    from app.models.organization import Shelf

    shelf = Shelf(name="Empty scope")
    db.add(shelf)
    db.commit()

    r = client.post(
        "/api/v1/ai/summaries",
        headers=auth_headers("editor"),
        json={"scope_type": "shelf", "scope_id": str(shelf.id)},
    )
    assert r.status_code == 400


# --- #10: scope-level local_llm path ---


def test_scope_summary_local_llm_falls_back_to_extractive(db_session) -> None:
    """Scope local_llm degrades to extractive when the LLM isn't enabled, recording provenance."""
    shelf = Shelf(name="LLM scope")
    db_session.add(shelf)
    db_session.flush()
    for i in range(2):
        work = Work(
            canonical_title=f"Paper {i}",
            normalized_title=f"paper {i}",
            abstract=f"Sentence {i}. Neural networks learn representations from data.",
        )
        db_session.add(work)
        db_session.flush()
        db_session.add(ShelfWork(shelf_id=shelf.id, work_id=work.id))
    db_session.commit()

    summary, count = summarize_scope(
        db_session, scope_type="shelf", scope_id=shelf.id, summary_type="local_llm"
    )
    assert count == 2
    assert summary.summary_type == "local_llm"
    assert summary.provider_used == "extractive"  # LLM not enabled → honest fallback
    assert summary.fallback is True
    assert summary.fallback_reason


def test_scope_summary_api_accepts_local_llm(client, auth_headers, db) -> None:
    """The endpoint no longer rejects local_llm (the #10 root cause: enum was extractive-only)."""
    from app.models.organization import Shelf, ShelfWork

    shelf = Shelf(name="scope llm api")
    db.add(shelf)
    db.flush()
    work = Work(canonical_title="P", normalized_title="p", abstract="One. Two. Three. Four.")
    db.add(work)
    db.flush()
    db.add(ShelfWork(shelf_id=shelf.id, work_id=work.id))
    db.commit()

    r = client.post(
        "/api/v1/ai/summaries",
        headers=auth_headers("editor"),
        json={"scope_type": "shelf", "scope_id": str(shelf.id), "summary_type": "local_llm"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["summary_type"] == "local_llm"
    assert body["provider_requested"] == "local_llm"


# --- L4: the scope summary honors the admin AI config's provider/model ---


def test_scope_summary_api_uses_configured_provider(client, auth_headers, db, monkeypatch) -> None:
    """With the admin AI config set to local_llm + a model, the endpoint (given no explicit
    summary_type) resolves to the model-based provider and uses it — no extractive fallback."""
    import app.services.summarization as summ
    from app.models.organization import Shelf, ShelfWork
    from app.services.ai_config import update_ai_config

    update_ai_config(db, changes={"summary_provider": "local_llm", "summary_model": "llama3"})
    db.commit()
    monkeypatch.setattr(
        summ,
        "_ollama_summarize",
        lambda text, *, model, base_url, opts=None: f"LLM summary via {model}",
    )
    # The scope reduce step (map-reduce, UX batch 4) goes through the raw generator.
    monkeypatch.setattr(
        summ,
        "_ollama_generate",
        lambda prompt, *, model, base_url, opts=None: f"LLM summary via {model}",
    )

    shelf = Shelf(name="cfg provider")
    db.add(shelf)
    db.flush()
    work = Work(canonical_title="P", normalized_title="p", abstract="One. Two. Three. Four.")
    db.add(work)
    db.flush()
    db.add(ShelfWork(shelf_id=shelf.id, work_id=work.id))
    db.commit()

    r = client.post(
        "/api/v1/ai/summaries",
        headers=auth_headers("editor"),
        json={"scope_type": "shelf", "scope_id": str(shelf.id)},  # no summary_type → resolve config
    )
    assert r.status_code == 201
    body = r.json()
    assert body["summary_type"] == "local_llm"
    assert body["provider_used"] == "local_llm"
    assert body["fallback"] is False
    assert body["model_name"] == "llama3"
    assert "LLM summary via llama3" in body["text"]


def test_scope_summary_map_reduce_prompts_and_chunking(db, monkeypatch) -> None:
    """UX batch 4: the scope summary (a) frames the prompt as a COLLECTION (never 'this paper'),
    (b) chunks per-paper digests instead of truncating, and (c) persists per-paper LLM summaries
    so the paper view gains them too."""
    import app.services.summarization as summ
    from app.models.organization import Shelf, ShelfWork
    from app.services.ai_config import update_ai_config
    from app.services.summarization import summarize_scope

    update_ai_config(db, changes={"summary_provider": "local_llm", "summary_model": "m1"})
    db.commit()
    prompts: list[str] = []

    def fake_generate(prompt, *, model, base_url, opts=None):
        prompts.append(prompt)
        if prompt.startswith("Summarize the following academic paper"):
            return "Digest sentence with several words in it. " * 8  # realistic-length digest
        return f"OUT-{len(prompts)}"

    monkeypatch.setattr(summ, "_ollama_generate", fake_generate)
    monkeypatch.setattr(summ, "LLM_INPUT_CHAR_BUDGET", 600)  # force multi-chunk with small input

    shelf = Shelf(name="mr shelf")
    db.add(shelf)
    db.flush()
    for i in range(6):
        w = Work(
            canonical_title=f"Paper {i}",
            normalized_title=f"paper {i}",
            abstract=("Sentence about topic. " * 10) + f"Unique point {i}.",
        )
        db.add(w)
        db.flush()
        db.add(ShelfWork(shelf_id=shelf.id, work_id=w.id))
        # Full-text papers (a GROBID body) feed the per-paper map/reduce; abstract-only papers are
        # instead folded into a single grouped paragraph (2026-07-16), so this test needs bodies.
        tei = (
            '<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body>'
            f"<div><head>Intro</head><p>{'Body sentence about the topic. ' * 8}Unique {i}.</p></div>"
            "</body></text></TEI>"
        )
        db.add(RawTeiDocument(file_id=uuid.uuid4(), work_id=w.id, source="grobid", tei_xml=tei))
    db.commit()

    summary, count = summarize_scope(
        db, scope_type="shelf", scope_id=shelf.id, summary_type="local_llm"
    )
    db.commit()
    assert count == 6
    assert summary.provider_used == "local_llm"
    assert summary.params["method"] == "map_reduce"
    assert summary.params["chunks"] >= 2  # digests didn't fit one budget → chunked, not truncated
    assert summary.params["scope_label"] == "mr shelf"
    # Per-paper map summaries were persisted (reusable in the paper view + next scope run).
    stored = db.scalars(
        select(Summary).where(Summary.entity_type == "work", Summary.summary_type == "local_llm")
    ).all()
    assert len(stored) == 6
    # The map prompts are the single-paper prompt; the reduce prompts are collection-framed.
    reduce_prompts = [p for p in prompts if "COLLECTION AS A WHOLE" in p or "part " in p[:200]]
    assert reduce_prompts, prompts
    final = [p for p in prompts if "COLLECTION AS A WHOLE" in p]
    assert final and "shelf" in final[-1]
    assert 'never write "this paper"' in final[-1]

    # Second run reuses the stored per-paper summaries (no new map calls) — only reduce calls.
    before = len(prompts)
    summarize_scope(db, scope_type="shelf", scope_id=shelf.id, summary_type="local_llm")
    map_calls_second_run = sum(
        1 for p in prompts[before:] if p.startswith("Summarize the following academic paper")
    )
    assert map_calls_second_run == 0


def test_scope_summary_api_defaults_to_extractive_without_model(client, auth_headers, db) -> None:
    """With no AI summary model configured (CI default), the endpoint resolves to the extractive
    engine and reports it honestly so the UI can show the "set a model" hint (L4)."""
    from app.models.organization import Shelf, ShelfWork

    shelf = Shelf(name="no model")
    db.add(shelf)
    db.flush()
    work = Work(canonical_title="P", normalized_title="p", abstract="One. Two. Three. Four.")
    db.add(work)
    db.flush()
    db.add(ShelfWork(shelf_id=shelf.id, work_id=work.id))
    db.commit()

    r = client.post(
        "/api/v1/ai/summaries",
        headers=auth_headers("editor"),
        json={"scope_type": "shelf", "scope_id": str(shelf.id)},  # no summary_type
    )
    assert r.status_code == 201
    body = r.json()
    assert body["summary_type"] == "extractive"
    assert body["provider_used"] == "extractive"
    assert body["fallback"] is False


# --- S15/S16: async routing for large scopes --------------------------------------------------------


def _mk_works(db, n: int) -> None:
    from app.models.work import Work

    for i in range(n):
        db.add(Work(canonical_title=f"P{i}", normalized_title=f"p{i}", abstract=f"Alpha beta {i}."))
    db.commit()


def test_scope_summary_is_queued_when_queue_available(
    client, auth_headers, db, monkeypatch
) -> None:
    """As of 2026-07-16 every scope summary enqueues a job (so it shows in the Jobs tab), not just
    library-sized scopes — even a 3-paper scope returns 202 when the queue is reachable."""
    from app.workers import queue as queue_mod

    _mk_works(db, 3)
    monkeypatch.setattr(queue_mod, "enqueue_scope_summary", lambda *a, **k: "summary-scope-library")
    resp = client.post(
        "/api/v1/ai/summaries",
        headers=auth_headers("owner"),
        json={"scope_type": "library"},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["queued"] is True and body["job_id"] == "summary-scope-library"
    assert body["work_count"] == 3


def test_scope_summary_inline_fallback_when_queue_down_reads_back(client, auth_headers, db) -> None:
    # The autouse _scope_summary_runs_inline fixture simulates a down queue → inline fallback.
    _mk_works(db, 2)
    resp = client.post(
        "/api/v1/ai/summaries", headers=auth_headers("owner"), json={"scope_type": "library"}
    )
    assert resp.status_code == 201
    assert resp.json()["queued"] is False and resp.json()["text"]
    latest = client.get(
        "/api/v1/ai/summaries/latest?scope_type=library", headers=auth_headers("owner")
    )
    assert latest.status_code == 200
    assert latest.json()["text"] == resp.json()["text"]
    assert latest.json()["work_count"] == 2


def test_large_scope_topics_are_queued(client, auth_headers, db, monkeypatch) -> None:
    from app.services.app_config import update_ai_scope_job_threshold
    from app.workers import queue as queue_mod

    update_ai_scope_job_threshold(db, value=1)
    _mk_works(db, 2)
    monkeypatch.setattr(queue_mod, "enqueue_scope_topics", lambda *a, **k: "topics-scope-library")
    resp = client.post(
        "/api/v1/ai/topics", headers=auth_headers("owner"), json={"scope_type": "library"}
    )
    assert resp.status_code == 202
    assert resp.json()["queued"] is True and resp.json()["job_id"] == "topics-scope-library"


def test_scope_jobs_run_with_the_requesting_users_visibility(db, monkeypatch) -> None:
    """The background job recomputes the actor's SEE-set — it must not widen visibility."""
    import contextlib

    from app.core.security import hash_password
    from app.models.ai import Summary
    from app.models.user import User
    from app.workers import jobs
    from sqlalchemy import select

    _mk_works(db, 2)
    actor = User(username="jobrunner", password_hash=hash_password("pw"), role="owner")
    db.add(actor)
    db.commit()

    @contextlib.contextmanager
    def _session():
        yield db

    import app.db.session as db_session

    monkeypatch.setattr(db_session, "SessionLocal", lambda: _session())
    result = jobs.summarize_scope_job("library", None, actor_user_id=str(actor.id))
    assert result is not None and result["work_count"] == 2
    assert db.scalar(select(Summary).where(Summary.entity_type == "library")) is not None


def test_rq_timeout_aborts_summary_instead_of_degrading(db, monkeypatch) -> None:
    """An RQ job timeout mid-LLM-call must ABORT the job with the real reason, not be swallowed
    by the degrade-to-extractive handler (the 'Work-horse terminated unexpectedly' failure: the
    monitor hard-killed the horse because the loop kept running past the deadline)."""
    import app.services.summarization as summ
    from app.services.ai_config import update_ai_config
    from rq.timeouts import JobTimeoutException

    db_session = db
    update_ai_config(db_session, changes={"summary_provider": "local_llm", "summary_model": "m1"})
    db_session.commit()

    def timing_out(*_a, **_k):
        raise JobTimeoutException("Task exceeded maximum timeout value (21600 seconds)")

    monkeypatch.setattr(summ, "_ollama_summarize", timing_out)
    monkeypatch.setattr(summ, "_ollama_generate", timing_out)

    work = Work(canonical_title="t2", normalized_title="t2", abstract="Abstract sentence.")
    db_session.add(work)
    db_session.commit()

    with pytest.raises(JobTimeoutException):
        summarize_work(db_session, work, summary_type="local_llm", detail="short")


def test_is_abort_exception_covers_cancel_and_timeout() -> None:
    from app.workers.queue import JobCancelled, is_abort_exception
    from rq.timeouts import JobTimeoutException

    assert is_abort_exception(JobCancelled("stop"))
    assert is_abort_exception(JobTimeoutException("timeout"))
    assert not is_abort_exception(ValueError("boom"))
