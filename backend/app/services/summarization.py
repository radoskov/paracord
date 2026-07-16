"""Local paper summarization (SPEC §8.14).

Tier 0 ("abstract") stores the work's abstract verbatim. Tier 1 ("extractive") runs a small
frequency-based extractive summarizer over the richest text available (abstract + GROBID body
text) — no network calls and no LLM, fully deterministic. Tier 2 ("local_llm") is an **opt-in**
abstractive summary via a local Ollama daemon; when it is disabled or unreachable it **degrades**
to the extractive engine while still recording the requested model + the source sections that fed
it, so the result is always honest about provenance and no hard dependency is introduced.

Every summary is persisted with provenance (``model_name`` + ``prompt_version``).
"""

import hashlib
import logging
import math
import re
import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.ai import Summary
from app.models.work import Work
from app.services.scope_resolution import resolve_scope_works

logger = logging.getLogger(__name__)

# Sentinel used as entity_id for library-scoped summaries (no real entity row).
_LIBRARY_SCOPE_ID = uuid.UUID(int=0)

SUPPORTED_SUMMARY_TYPES = ("abstract", "extractive", "local_llm")
PROMPT_VERSION = "v1"
LLM_PROMPT_VERSION = "local-llm-v2-map-reduce"

# Per-LLM-call input budget (characters, ≈ chars/4 tokens). Conservative on purpose: small local
# models often run with a 4-8k-token context. Scope summaries CHUNK to this budget (map-reduce)
# instead of silently truncating like v1 did.
LLM_INPUT_CHAR_BUDGET = 11000

# A deliberately small English stop-word set; enough to bias scoring toward content words
# without pulling in an NLP dependency.
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "their",
        "then",
        "there",
        "these",
        "this",
        "to",
        "was",
        "were",
        "which",
        "with",
        "we",
        "our",
        "this",
        "these",
        "those",
        "they",
        "them",
        "he",
        "she",
        "his",
        "her",
        "you",
        "your",
        "i",
        "but",
        "not",
        "can",
        "will",
        "may",
        "also",
        "such",
        "using",
        "used",
        "use",
        "based",
        "both",
        "than",
        "been",
        "being",
        "more",
        "most",
    ]
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"[A-Za-z][A-Za-z'-]+")

# 2026-07-16: ask the model to delimit maths so the UI can render it with KaTeX (fancy mode). Kept
# terse and appended to the end of prompts so existing prompt-prefix assertions still hold.
_MATH_HINT = " Write any mathematical expression in LaTeX between $…$ (inline) or $$…$$ (display)."


def summarize_extractive(text: str, *, max_sentences: int = 5) -> str:
    """Return the highest-scoring sentences (in original order) by word-frequency salience."""
    cleaned = " ".join((text or "").split())
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(cleaned) if s.strip()]
    if len(sentences) <= max_sentences:
        return cleaned

    frequencies: dict[str, int] = {}
    for word in _WORD.findall(cleaned.lower()):
        if word not in _STOPWORDS:
            frequencies[word] = frequencies.get(word, 0) + 1
    if not frequencies:
        return " ".join(sentences[:max_sentences])
    peak = max(frequencies.values())

    scored: list[tuple[float, int]] = []
    for index, sentence in enumerate(sentences):
        words = [w for w in _WORD.findall(sentence.lower()) if w not in _STOPWORDS]
        if not words:
            continue
        # Average normalized salience, damped by length so long sentences don't always win.
        salience = sum(frequencies[w] / peak for w in words) / math.sqrt(len(words))
        scored.append((salience, index))

    top_indices = sorted(index for _, index in sorted(scored, reverse=True)[:max_sentences])
    return " ".join(sentences[index] for index in top_indices)


def _work_source(db: Session, work: Work) -> tuple[str, list[str]]:
    """Gather the richest text available for a work plus its provenance labels.

    Returns ``(text, labels)``: the abstract plus the GROBID body — but the body is built from the
    section-filtered ``iter_work_sections`` (2026-07-16), so references + boilerplate (Funding,
    Acknowledgements, Conflicts, …) never leak into ANY summary, not just the detailed ones.
    ``labels`` records which sources are present (abstract / body).
    """
    from app.services.chunking import iter_work_sections  # noqa: PLC0415 (avoid import cycle)

    parts: list[str] = []
    labels: list[str] = []
    if work.abstract:
        parts.append(work.abstract)
        labels.append("abstract")
    # title/abstract come from the work fields above; the rest are the filtered TEI body sections.
    body = "\n".join(
        text for label, text in iter_work_sections(db, work) if label not in ("title", "abstract")
    ).strip()
    if body:
        parts.append(body)
        labels.append("body")
    return "\n".join(parts).strip(), labels


def work_source_text(db: Session, work: Work) -> str:
    """Gather the richest text available for a work: its abstract plus GROBID body text."""
    return _work_source(db, work)[0]


# 2026-07-16 no-PDF honesty: classify what a paper can actually be summarized FROM, so the summary
# is framed accordingly instead of silently falling back to the title.
#   full_text     — has GROBID body text (a processed PDF); summarize normally.
#   abstract_only — has an abstract but no body; summarize the abstract, framed as such.
#   title_only    — neither; cannot be summarized locally.
def _source_tier(labels: list[str]) -> str:
    if "body" in labels:
        return "full_text"
    if "abstract" in labels:
        return "abstract_only"
    return "title_only"


def classify_work_source(db: Session, work: Work) -> str:
    """Public: which source tier a work can be summarized from (see ``_source_tier``)."""
    return _source_tier(_work_source(db, work)[1])


def _ollama_generate(prompt: str, *, model: str, base_url: str) -> str:
    """Raw single-shot generation against a local Ollama daemon. Raises on any failure."""
    import httpx2 as httpx

    with httpx.Client(timeout=120) as client:
        response = client.post(
            f"{base_url.rstrip('/')}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
        )
        response.raise_for_status()
        return (response.json().get("response") or "").strip()


def _ollama_summarize(text: str, *, model: str, base_url: str, abstract_only: bool = False) -> str:
    """Per-PAPER SHORT abstractive summary (single-paragraph prompt). Raises on any failure.

    ``abstract_only`` (2026-07-16): the source is the paper's abstract, not its full text — tell the
    model so it frames the summary as coverage of what the abstract states, WITHOUT fixating on the
    fact that it's only an abstract.
    """
    if abstract_only:
        prompt = (
            "You are given only the ABSTRACT of an academic paper (its full text is unavailable). "
            "Summarize what the abstract conveys in 2-3 sentences — the problem, approach, and "
            "stated result. Write naturally; do not dwell on the fact that this is an abstract. "
            "Respond with the summary only.\n\n" + text[:LLM_INPUT_CHAR_BUDGET]
        )
    else:
        prompt = (
            "Summarize the following academic paper text in 3-4 sentences, focusing on the problem, "
            "method, and key result. Respond with the summary only."
            + _MATH_HINT
            + "\n\n"
            + text[:LLM_INPUT_CHAR_BUDGET]
        )
    return _ollama_generate(prompt, model=model, base_url=base_url)


def _detail_chunk_prompt(label: str | None) -> str:
    named = f'the "{label}" section' if label else "this section"
    return (
        f"Summarize {named} of an academic paper into a single focused paragraph covering what it "
        "establishes (problem, method, data, or result). Start directly with the content — do NOT "
        'begin with "This section". Respond with the paragraph only.' + _MATH_HINT + "\n\n"
    )


_DETAIL_INTRO_PROMPT = (
    "The paragraphs below are section-by-section summaries of ONE academic paper. Write a 2-3 "
    "sentence high-level overview of the whole paper (problem, approach, key result). Respond "
    "with the overview only.\n\n"
)


def _split_to_budget(block: str, budget: int) -> list[str]:
    """Split a block longer than ``budget`` on sentence boundaries into ≤budget pieces."""
    if len(block) <= budget:
        return [block]
    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE_SPLIT.split(block):
        if current and len(current) + len(sentence) > budget:
            pieces.append(current.strip())
            current = ""
        current += sentence + " "
    if current.strip():
        pieces.append(current.strip())
    return pieces or [block[:budget]]


def _paragraphs(text: str, budget: int) -> list[str]:
    """Split body text into map blocks: paragraph breaks first, then sentence-split any block
    still over ``budget`` (GROBID body extraction often drops paragraph breaks, leaving one blob).
    """
    raw = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    raw = raw or ([text.strip()] if text.strip() else [])
    out: list[str] = []
    for block in raw:
        out.extend(_split_to_budget(block, budget))
    return out


def _detailed_llm_summary(
    sections: list[tuple[str | None, str]],
    *,
    model: str,
    base_url: str,
    progress_cb=None,
    cancel_cb=None,
) -> str:
    """Per-PAPER DETAILED summary (UX batch 4): one paragraph PER SECTION, each headed by the
    section's name (from GROBID) so the reader sees which part of the paper it covers, plus a
    high-level intro synthesized from the section paragraphs. The whole body is used (no 12k clip);
    an over-long section is split to the context budget and its pieces are joined. Sections are
    rendered as ``"Label: <paragraph>"``. Raises on any LLM failure.

    ``progress_cb(done, total)`` and ``cancel_cb() -> bool`` let the background job report
    per-section progress and stop cooperatively (2026-07-16); the intro is the final step.
    """
    # +1 for the synthesized intro step so the progress bar reaches total only when fully done.
    total = len(sections) + 1
    out: list[str] = []
    for idx, (label, text) in enumerate(sections):
        if cancel_cb is not None and cancel_cb():
            from app.workers.queue import JobCancelled  # noqa: PLC0415 (cycle guard)

            raise JobCancelled(f"cancelled after {idx} of {len(sections)} sections")
        if progress_cb is not None:
            progress_cb(idx, total)
        text = (text or "").strip()
        if not text:
            continue
        pieces = _split_to_budget(text, LLM_INPUT_CHAR_BUDGET)
        parts = [
            _ollama_generate(_detail_chunk_prompt(label) + piece, model=model, base_url=base_url)
            for piece in pieces
        ]
        para = " ".join(p.strip() for p in parts if p.strip()).strip()
        if not para:
            continue
        out.append(f"{label}: {para}" if label else para)
    if not out:
        return ""
    if len(out) == 1:
        if progress_cb is not None:
            progress_cb(total, total)
        return out[0]
    if progress_cb is not None:
        progress_cb(len(sections), total)  # entering the final intro-synthesis step
    intro = _ollama_generate(
        _DETAIL_INTRO_PROMPT + "\n\n".join(out)[:LLM_INPUT_CHAR_BUDGET],
        model=model,
        base_url=base_url,
    )
    if progress_cb is not None:
        progress_cb(total, total)
    body = "\n\n".join(out)
    return f"{intro}\n\n{body}" if intro.strip() else body


# --- Detailed effort levels (2026-07-16) -----------------------------------------------------
# A detailed summary can be expensive on a small GPU (one LLM call per section). Three effort
# levels trade granularity for cost, stored as separate rows so the cache holds all of them:
#   detailed_fast    — group sections into ≤4 buckets, one call per bucket (cheapest).
#   detailed_section — one call per top-level section (medium).
#   detailed_deep    — one call per subsection (finest; the historical "detailed").
_DETAIL_SUFFIX = {
    "short": "",
    "detailed_fast": "_detailed_fast",
    "detailed_section": "_detailed_section",
    "detailed_deep": "_detailed_deep",
}
DETAIL_LEVELS = tuple(_DETAIL_SUFFIX)


def _normalize_detail(detail: str) -> str:
    """Validate a detail level; map the legacy ``"detailed"`` to ``"detailed_deep"``."""
    if detail == "detailed":  # back-compat: the pre-2026-07-16 single detailed level == deep
        return "detailed_deep"
    if detail not in _DETAIL_SUFFIX:
        raise ValueError(f"Unsupported detail level: {detail}")
    return detail


def stored_summary_type(base: str, detail: str) -> str:
    """The DB ``summary_type`` for a base provider + detail level (public: used by the API to key
    the cache matrix, e.g. ``("local_llm", "detailed_deep") -> "local_llm_detailed_deep"``)."""
    return base + _DETAIL_SUFFIX[_normalize_detail(detail)]


# 2026-07-16 cache matrix: keep up to N models' summaries per (entity, detail level) so switching
# the AI model and back is instant; evict the least-recently-generated model beyond that.
SUMMARY_MODEL_CACHE = 5


def _evict_stale_models(
    db: Session, entity_type: str, entity_id, stored_type: str, *, keep: int | None = None
) -> None:
    """Trim the (entity, summary_type) cache to the ``keep`` most-recent distinct models (LRU)."""
    keep = keep if keep is not None else SUMMARY_MODEL_CACHE  # read at call time (monkeypatchable)
    rows = db.execute(
        select(Summary.model_name)
        .where(
            Summary.entity_type == entity_type,
            Summary.entity_id == entity_id,
            Summary.summary_type == stored_type,
        )
        .group_by(Summary.model_name)
        .order_by(func.max(Summary.created_at).desc())
    ).all()
    stale = [r[0] for r in rows[keep:]]
    if stale:
        db.execute(
            delete(Summary).where(
                Summary.entity_type == entity_type,
                Summary.entity_id == entity_id,
                Summary.summary_type == stored_type,
                Summary.model_name.in_(stale),
            )
        )


_FAST_BUCKETS = ("Background", "Methods", "Results", "Other")
_FAST_KEYWORDS = {
    "Background": [
        "title",
        "abstract",
        "introduction",
        "intro",
        "related",
        "background",
        "motivation",
        "preliminar",
        "overview",
    ],
    "Methods": [
        "method",
        "approach",
        "model",
        "architecture",
        "implementation",
        "experiment",
        "setup",
        "dataset",
        "data",
        "algorithm",
        "design",
        "framework",
    ],
    "Results": [
        "result",
        "discussion",
        "conclusion",
        "evaluation",
        "analysis",
        "finding",
        "appendix",
        "future",
        "limitation",
    ],
}


def _heuristic_bucket(label: str | None) -> str:
    low = (label or "").lower()
    for bucket in ("Background", "Methods", "Results"):
        if any(kw in low for kw in _FAST_KEYWORDS[bucket]):
            return bucket
    return "Other"


def _categorize_sections(labels: list[str], *, model: str, base_url: str) -> dict[str, str]:
    """Ask the LLM to map each section label to one of the 4 fast buckets; heuristic on failure.

    Returns a partial ``{label: bucket}``; callers fall back to :func:`_heuristic_bucket` for any
    label the LLM didn't classify, so a bad/absent LLM answer degrades gracefully.
    """
    labels = [lbl for lbl in labels if lbl]
    if not labels:
        return {}
    listing = "\n".join(f"{i + 1}. {lbl}" for i, lbl in enumerate(labels))
    prompt = (
        "Classify each academic-paper section title into exactly ONE category: Background, "
        "Methods, Results, or Other.\n"
        "- Background: title, abstract, introduction, related work, motivation.\n"
        "- Methods: methods, implementation, experiments, datasets, model/architecture.\n"
        "- Results: results, discussion, conclusion, appendix.\n"
        "- Other: anything else.\n"
        "Respond with one line per section as 'N: Category'.\n\n" + listing
    )
    try:
        raw = _ollama_generate(prompt, model=model, base_url=base_url)
    except Exception as exc:  # noqa: BLE001 - heuristic fallback, never fail the summary
        logger.warning("section categorizer LLM unavailable (%s); using heuristic", exc)
        return {}
    mapping: dict[str, str] = {}
    for line in raw.splitlines():
        m = re.match(r"\s*(\d+)\s*[:.)\-]\s*([A-Za-z]+)", line)
        if not m:
            continue
        idx = int(m.group(1)) - 1
        cat = m.group(2).capitalize()
        if 0 <= idx < len(labels) and cat in _FAST_BUCKETS:
            mapping[labels[idx]] = cat
    return mapping


def _fast_bucket_sections(db: Session, work: Work, *, model: str, base_url: str):
    """detailed_fast: fold the work's sections into ≤4 buckets, each summarized as one paragraph."""
    from app.services.chunking import iter_work_sections  # noqa: PLC0415 (cycle)

    secs = iter_work_sections(db, work)
    mapping = _categorize_sections([lbl for lbl, _ in secs if lbl], model=model, base_url=base_url)
    grouped: dict[str, list[str]] = {b: [] for b in _FAST_BUCKETS}
    for label, text in secs:
        bucket = mapping.get(label) or _heuristic_bucket(label)
        if text:
            grouped[bucket].append(text)
    return [(b, " ".join(grouped[b])) for b in _FAST_BUCKETS if grouped[b]]


_ROMAN_RE = re.compile(r"^[IVXLCDM]+$")
_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
# Leading section marker: roman ("II"), a single capital letter ("A"), or arabic dotted ("2.1"),
# optionally followed by a '.'/')' then a space + the title. Section labels carry the number either
# inline (GROBID inline heads) or prefixed from the head @n (see tei_parser._section_label).
_MARKER_RE = re.compile(r"^\s*([IVXLCDM]+|[A-Z]|\d+(?:\.\d+)*)[.)]?\s+\S")


def _roman_to_int(s: str) -> int | None:
    total = prev = 0
    for ch in reversed(s):
        v = _ROMAN_VALUES.get(ch)
        if v is None:
            return None
        total += -v if v < prev else v
        prev = max(prev, v)
    return total or None


def _section_marker(label: str | None) -> str | None:
    m = _MARKER_RE.match(label or "")
    return m.group(1) if m else None


def _coalesce_main_sections(sections):
    """Group flat GROBID sections into MAIN sections (2026-07-16), folding subsections into their
    parent so the "section" level summarizes the main sections (e.g. 13, not 33).

    Robust to numbering scheme: arabic ("1", "1.1"), roman-with-letter-subs ("I"…/"A"…), inline
    numbers, or @n-prefixed labels. The scheme is taken from the FIRST section with a recognizable
    number (usually the Introduction). A MAIN section advances the running sequence by one (a single
    skip tolerated); anything else — a subsection ("1.1", "A"), an unnumbered running header, or a
    far-off value like roman "C"=100 — folds into the current main. A paper with no recognizable
    section numbers is returned unchanged."""
    markers = [(_section_marker(lbl), lbl, txt) for lbl, txt in sections]
    scheme: str | None = None
    for tok, _, _ in markers:
        if tok is None:
            continue
        if tok.isdigit():
            scheme = "arabic"
            break
        if _ROMAN_RE.match(tok) and _roman_to_int(tok):
            scheme = "roman"
            break
        # a leading single letter ("A") as the very first marker is ambiguous — keep looking
    if scheme is None:
        return sections
    out: list[tuple[str | None, str]] = []
    current = 0
    lead = ""  # un-numbered lead-in before the first main section (folded into it)
    for tok, lbl, txt in markers:
        value: int | None = None
        if tok is not None:
            if scheme == "arabic" and tok.isdigit():
                value = int(tok)
            elif scheme == "roman" and _ROMAN_RE.match(tok):
                value = _roman_to_int(tok)
        is_main = value is not None and current < value <= current + 2
        if is_main:
            current = value
            if not out and lead:
                txt = f"{lead} {txt}".strip()
                lead = ""
            out.append((lbl, txt))
        elif out:
            out[-1] = (out[-1][0], f"{out[-1][1]} {txt}".strip())
        else:
            lead = f"{lead} {txt}".strip()
    # No main ever matched (numbering detected but sequence never advanced) → leave untouched.
    return out or sections


def _section_level_sections(db: Session, work: Work):
    """detailed_section: one entry per MAIN section (subsections folded in by numbering). If that
    still yields fewer than 3, drop to the finer subsection list (iff <10) so a coarsely-parsed
    paper still gets useful granularity."""
    from app.services.chunking import (  # noqa: PLC0415 (cycle)
        iter_work_leaf_sections,
        iter_work_sections,
    )

    top = [(lbl, t) for lbl, t in iter_work_sections(db, work) if lbl != "title"]
    mains = _coalesce_main_sections(top)
    if len(mains) < 3 and len(top) < 10:
        leaf = [(lbl, t) for lbl, t in iter_work_leaf_sections(db, work) if lbl != "title"]
        if leaf:
            return leaf
    return mains


def _detail_sections(db: Session, work: Work, detail: str, *, model: str, base_url: str):
    """The (label, text) sections that feed the detailed summary for a given effort level."""
    if detail == "detailed_fast":
        return _fast_bucket_sections(db, work, model=model, base_url=base_url)
    if detail == "detailed_section":
        return _section_level_sections(db, work)
    from app.services.chunking import iter_work_leaf_sections  # noqa: PLC0415 (cycle)

    return [(lbl, t) for lbl, t in iter_work_leaf_sections(db, work) if lbl != "title"]


# --- Scope (collection) prompts — UX batch 4 -------------------------------------------------
# v1 fed the concatenated abstracts to the PAPER prompt, so the model opened with "This paper
# addresses…" for a whole shelf. The scope prompts below make the collection framing explicit and
# ask for the cross-collection view (problems / methods / datasets / findings), with a high-level
# overview paragraph first.

_SCOPE_DESCRIPTOR = {
    "library": "the user's entire research library",
    "shelf": "one shelf — a curated collection of papers",
    "rack": "one rack — a group of related shelves of papers",
}


def _scope_final_prompt(scope_type: str, paper_count: int, notes: str, *, from_parts: bool) -> str:
    descriptor = _SCOPE_DESCRIPTOR.get(scope_type, "a collection of papers")
    source_line = (
        "Below are condensed notes derived from the papers' individual summaries."
        if from_parts
        else 'Below are short per-paper summaries, one per line ("Title (year): summary").'
    )
    return (
        f"You are synthesizing {descriptor}, containing {paper_count} papers. {source_line}\n"
        "Write a synthesis of the COLLECTION AS A WHOLE — it is a set of papers, not one paper, "
        'so never write "this paper". Structure the answer as:\n'
        "1. A short high-level overview paragraph of what the collection covers and how the "
        "papers relate.\n"
        "2. Key problems: the main research problems addressed across the collection.\n"
        "3. Methods & algorithms: recurring or notable approaches.\n"
        "4. Datasets & benchmarks: any that are mentioned.\n"
        "5. Key findings: the most important results, attributed to their papers by title where "
        "specific.\n"
        "Be concise but do not omit distinct contributions. Respond with the synthesis only.\n\n"
        + notes
    )


def _scope_chunk_prompt(scope_type: str, part: int, parts: int, notes: str) -> str:
    descriptor = _SCOPE_DESCRIPTOR.get(scope_type, "a collection of papers")
    return (
        f"The following are per-paper summaries from part {part} of {parts} of {descriptor}. "
        "Condense them into compact notes covering the research problems, methods/algorithms, "
        "datasets/benchmarks and key findings, KEEPING the paper-title attributions. Respond "
        "with the condensed notes only.\n\n" + notes
    )


def _pack_blocks(blocks: list[str], budget: int) -> list[str]:
    """Greedily pack digest blocks into chunks of at most ``budget`` characters (≥1 block each)."""
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for block in blocks:
        if current and size + len(block) > budget:
            chunks.append("\n".join(current))
            current = []
            size = 0
        current.append(block)
        size += len(block) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def summarize_work(
    db: Session,
    work: Work,
    *,
    summary_type: str = "extractive",
    detail: str = "short",
    max_sentences: int = 5,
    model_name: str | None = None,
    created_by_user_id: uuid.UUID | None = None,
    settings: Settings | None = None,
    progress_cb=None,
    cancel_cb=None,
) -> Summary:
    """Create (replacing the prior summary of the same type+detail+model) a provenance-tagged summary.

    ``detail`` (UX batch 4 / 2026-07-16): ``"short"`` = one paragraph (historical); the detailed
    levels ``"detailed_fast"`` / ``"detailed_section"`` / ``"detailed_deep"`` trade cost for
    granularity (``"detailed"`` is a back-compat alias for deep). Each (detail × model) is stored as
    a SEPARATE row so the paper view can offer a cache of all of them; up to 5 models are kept per
    detail level (LRU).

    For ``local_llm`` the returned Summary carries a transient ``source_sections`` attribute (the
    sources that fed it); the API surfaces it. When the LLM is disabled/unreachable the text is the
    extractive fallback but the requested model is still recorded, with a fallback note in
    ``source_sections``.
    """
    if summary_type not in SUPPORTED_SUMMARY_TYPES:
        raise ValueError(f"Unsupported summary type: {summary_type}")
    detail = _normalize_detail(detail)
    # Each detail level is stored under a distinct type so they never clobber each other.
    stored_type = summary_type + _DETAIL_SUFFIX[detail]
    detailed = detail != "short"
    # A longer extractive stands in for a detailed summary when the LLM isn't producing it.
    eff_sentences = max_sentences * 3 if detailed else max_sentences

    settings = settings or get_settings()
    from app.services.ai_config import get_ai_config  # noqa: PLC0415 (avoid import cycle)

    ai_cfg = get_ai_config(db, settings=settings)
    source_sections: list[str] = []
    prompt_version = PROMPT_VERSION
    # Provider provenance (Phase B2): what was requested vs actually used, and — when a requested
    # LLM silently degraded to the extractive engine — a short reason the UI can show.
    provider_requested = summary_type
    provider_used = summary_type
    fallback_reason: str | None = None

    if summary_type == "abstract":
        text = (work.abstract or "").strip()
        stored_model = "tier0-abstract"
    elif summary_type == "local_llm":
        stored_model = model_name or ai_cfg.summary_model
        prompt_version = LLM_PROMPT_VERSION
        source_text, source_sections = _work_source(db, work)
        tier = _source_tier(source_sections)
        # 2026-07-16 no-PDF honesty: a title-only paper has no summarizable content — say so
        # plainly rather than silently "summarizing" the title.
        if tier == "title_only":
            raise ValueError(
                "This paper has only a title (no abstract or full text) and cannot be summarized."
            )
        abstract_only = tier == "abstract_only"
        if abstract_only:
            source_sections.append("abstract-only")
        text = ""
        # The LLM is attempted only when the owner has selected it (ai_config) — otherwise we go
        # straight to the deterministic fallback, so disabled/CI installs never hit the network.
        if ai_cfg.summary_provider == "local_llm" and source_text:
            try:
                if detailed and not abstract_only:
                    # Section-by-section over the GROBID sections (granularity chosen by the effort
                    # level) so each paragraph is headed by its section name. Abstract-only papers
                    # have no body to section, so they take the abstract-framed short path below
                    # regardless of the requested detail.
                    sections = _detail_sections(
                        db, work, detail, model=stored_model, base_url=ai_cfg.ollama_url
                    )
                    text = _detailed_llm_summary(
                        sections,
                        model=stored_model,
                        base_url=ai_cfg.ollama_url,
                        progress_cb=progress_cb,
                        cancel_cb=cancel_cb,
                    )
                else:
                    text = _ollama_summarize(
                        source_text,
                        model=stored_model,
                        base_url=ai_cfg.ollama_url,
                        abstract_only=abstract_only,
                    )
            except Exception as exc:  # noqa: BLE001 - degrade to extractive, never fail the request
                from app.workers.queue import JobCancelled  # noqa: PLC0415 (cycle guard)

                if isinstance(exc, JobCancelled):
                    raise  # a user Stop must abort the job, not silently fall back to extractive
                logger.warning("local_llm summary unavailable (%s); using extractive fallback", exc)
                fallback_reason = str(exc) or "the local LLM is unavailable"
        else:
            fallback_reason = "the local LLM is not enabled"
        if not text:
            text = summarize_extractive(source_text, max_sentences=eff_sentences)
            provider_used = "extractive"
            if text:
                source_sections.append("extractive-fallback")
    else:
        text = summarize_extractive(work_source_text(db, work), max_sentences=eff_sentences)
        stored_model = "tier1-extractive-frequency"

    if not text:
        raise ValueError("No text available to summarize")

    # One stored summary per (work, type+detail, MODEL): replace the same combo so re-runs stay
    # idempotent, but a different model coexists (the cache matrix).
    db.execute(
        delete(Summary).where(
            Summary.entity_type == "work",
            Summary.entity_id == work.id,
            Summary.summary_type == stored_type,
            Summary.model_name == stored_model,
        )
    )
    fallback = provider_used != provider_requested
    summary = Summary(
        entity_type="work",
        entity_id=work.id,
        summary_type=stored_type,
        text=text,
        model_name=stored_model,
        prompt_version=prompt_version,
        provider_requested=provider_requested,
        provider_used=provider_used,
        fallback=fallback,
        source_sections=source_sections,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        created_by_user_id=created_by_user_id,
        params={
            "summary_type": summary_type,
            "detail": detail,
            "max_sentences": max_sentences,
            "model_name": model_name,
        },
    )
    db.add(summary)
    db.flush()
    _evict_stale_models(db, "work", work.id, stored_type)
    # A ``fallback_reason`` is transient (not a column): the UI shows it only on a fresh degrade.
    summary.fallback_reason = fallback_reason if fallback else None
    return summary


def list_work_summaries(db: Session, work_id: uuid.UUID) -> list[Summary]:
    """Return stored summaries for a work, newest first."""
    return list(
        db.scalars(
            select(Summary)
            .where(Summary.entity_type == "work", Summary.entity_id == work_id)
            .order_by(Summary.created_at.desc())
        ).all()
    )


# Scope resolution is shared now (S1/S2) — one query-returning resolver, required
# visibility clamp, shadow filter applied centrally.
def _scope_works(db, *, scope_type, scope_id, visible_ids):
    return resolve_scope_works(db, scope_type, scope_id, visible_ids=visible_ids)


def _work_digest(
    db: Session,
    work: Work,
    *,
    use_llm: bool,
    model_name: str,
    created_by_user_id: uuid.UUID | None,
    settings: Settings | None,
    detail: str = "short",
    regenerate: bool = False,
) -> tuple[str, str | None] | None:
    """One paper's digest for the scope map step. Returns ``(text, degraded_reason)`` or None.

    UX batch 4: ``detail`` picks which per-paper summary feeds the scope synthesis ("short" or
    "detailed"); ``regenerate`` forces a fresh one. When not regenerating, an existing genuine
    local_llm summary of that detail level is reused (so a scope run doesn't re-summarize papers
    already summarized in their own view, and vice versa). ``degraded_reason`` is non-None when the
    LLM attempt fell back — the scope loop uses it to stop hammering an unavailable daemon.
    """
    stored_type = "local_llm" + _DETAIL_SUFFIX[_normalize_detail(detail)]
    if use_llm:
        if not regenerate:
            # Reuse an existing genuine summary for THIS model + detail (the cache matrix), so a
            # scope run doesn't re-summarize papers already summarized under the same model.
            existing = db.scalars(
                select(Summary)
                .where(
                    Summary.entity_type == "work",
                    Summary.entity_id == work.id,
                    Summary.summary_type == stored_type,
                    Summary.model_name == model_name,
                )
                .order_by(Summary.created_at.desc())
            ).first()
            # Don't reuse a STALE summary: if the paper has since gained full text but the stored
            # summary was made from the abstract only, regenerate it (2026-07-16 — "new PDFs").
            stale = (
                existing is not None
                and "abstract-only" in (existing.source_sections or [])
                and _source_tier(_work_source(db, work)[1]) == "full_text"
            )
            if (
                existing is not None
                and not stale
                and existing.provider_used == "local_llm"
                and existing.text
            ):
                return existing.text, None
        try:
            s = summarize_work(
                db,
                work,
                summary_type="local_llm",
                detail=detail,
                model_name=model_name,
                created_by_user_id=created_by_user_id,
                settings=settings,
            )
        except ValueError:
            return None  # nothing to summarize for this paper
        if s.fallback:
            reason = getattr(s, "fallback_reason", None) or "the local LLM is unavailable"
            return s.text, reason
        return s.text, None
    if work.abstract:
        return work.abstract.strip(), None
    text = work_source_text(db, work)
    if not text:
        return None
    return summarize_extractive(text, max_sentences=4), None


def _scope_label(db: Session, scope_type: str, scope_id: uuid.UUID | None) -> str | None:
    """Human label for the summarized scope ("whole library" / shelf name / rack name)."""
    if scope_type == "library":
        return "whole library"
    if scope_id is None:
        return None
    from app.models.organization import Rack, Shelf  # noqa: PLC0415 (avoid import cycle)

    container = db.get(Shelf if scope_type == "shelf" else Rack, scope_id)
    return getattr(container, "name", None)


def latest_scope_summary(
    db: Session,
    *,
    scope_type: str,
    scope_id: uuid.UUID | None,
    summary_type: str | None = None,
    model_name: str | None = None,
):
    """The most recent stored summary for a scope, or None (read path for the S15 async flow).

    ``summary_type`` (an effort-encoded type e.g. ``local_llm_detailed_deep``) and ``model_name``
    narrow the lookup to a specific cache-matrix cell (2026-07-16)."""
    entity_id = scope_id if scope_id is not None else _LIBRARY_SCOPE_ID
    conditions = [Summary.entity_type == scope_type, Summary.entity_id == entity_id]
    if summary_type is not None:
        conditions.append(Summary.summary_type == summary_type)
    if model_name is not None:
        conditions.append(Summary.model_name == model_name)
    return db.scalars(
        select(Summary).where(*conditions).order_by(Summary.created_at.desc())
    ).first()


_TITLE_ONLY_LIST_CAP = 30  # keep the title-only paragraph readable in a huge scope


def _work_ref(work: Work) -> str:
    title = (work.canonical_title or "Untitled").strip()
    return f"{title} ({work.year})" if work.year else title


def _abstract_only_paragraph(works: list[Work], *, use_llm: bool, model: str, base_url: str) -> str:
    """2026-07-16: ONE grouped paragraph for papers available only as an abstract (no full text)."""
    entries = [f"{_work_ref(w)}: {(w.abstract or '').strip()}" for w in works]
    lead = (
        f"{len(works)} paper(s) in this collection are available only as abstracts "
        "(no full text was retrieved)"
    )
    if use_llm:
        try:
            body = _ollama_generate(
                f"The following {len(works)} papers are available ONLY as abstracts (no full "
                "text). In a SINGLE paragraph, summarize collectively what they address and how "
                "they relate, keeping title attributions where useful. Respond with the paragraph "
                "only.\n\n" + "\n".join(entries)[:LLM_INPUT_CHAR_BUDGET],
                model=model,
                base_url=base_url,
            )
            if body.strip():
                return f"{lead}. {body.strip()}"
        except Exception as exc:  # noqa: BLE001 - fall back to extractive over the abstracts
            logger.warning("abstract-only paragraph LLM unavailable (%s); extractive fallback", exc)
    ex = summarize_extractive(" ".join((w.abstract or "") for w in works), max_sentences=5)
    return f"{lead}. {ex}".strip()


def _title_only_paragraph(works: list[Work]) -> str:
    """2026-07-16: ONE grouped paragraph naming papers that are title-only (unsummarizable)."""
    refs = [_work_ref(w) for w in works]
    shown = refs[:_TITLE_ONLY_LIST_CAP]
    more = f", and {len(refs) - len(shown)} more" if len(refs) > len(shown) else ""
    return (
        f"{len(works)} paper(s) are listed by title only (no abstract or full text available) and "
        f"were not summarized: " + "; ".join(shown) + more + "."
    )


def summarize_scope(
    db: Session,
    *,
    scope_type: str,
    scope_id: uuid.UUID | None = None,
    summary_type: str = "extractive",
    max_sentences: int = 8,
    model_name: str | None = None,
    created_by_user_id: uuid.UUID | None = None,
    visible_ids: set[uuid.UUID] | None = None,
    settings: Settings | None = None,
    paper_detail: str = "short",
    regenerate_papers: bool = False,
    progress_cb=None,
    cancel_cb=None,
) -> tuple[Summary, int]:
    """Generate (replacing prior) a scope summary over the scope's papers.

    ``local_llm`` builds per-paper digests (map) then synthesizes the collection (reduce); UX
    batch 4 adds ``paper_detail`` ("short"/"detailed") to choose which per-paper summary feeds the
    map, and ``regenerate_papers`` to force those per-paper summaries to be regenerated rather than
    reused. Degrades to the extractive engine on any LLM failure. Returns ``(summary, work_count)``;
    ``visible_ids`` (Phase H) restricts the scope to works the caller may see.
    """
    if summary_type not in SUPPORTED_SUMMARY_TYPES:
        raise ValueError(f"Unsupported summary type: {summary_type}")
    settings = settings or get_settings()
    from app.services.ai_config import get_ai_config  # noqa: PLC0415 (avoid import cycle)

    ai_cfg = get_ai_config(db, settings=settings)
    works = _scope_works(db, scope_type=scope_type, scope_id=scope_id, visible_ids=visible_ids)
    entity_id = scope_id if scope_id is not None else _LIBRARY_SCOPE_ID
    # 2026-07-16 cache matrix: the scope summary's effort level (paper_detail) is folded into its
    # stored_type so short/fast/section/deep coexist per scope; extractive scopes have no effort.
    scope_detail = _normalize_detail(paper_detail)
    stored_type = summary_type + (
        _DETAIL_SUFFIX[scope_detail] if summary_type == "local_llm" else ""
    )

    abstracts = [w.abstract for w in works if w.abstract]
    if not abstracts and summary_type != "local_llm":
        raise ValueError(f"No abstracts available in {scope_type!r} scope to summarize")
    combined = " ".join(abstracts)

    provider_requested = summary_type
    provider_used = summary_type
    fallback_reason: str | None = None
    prompt_version = PROMPT_VERSION
    method = "extractive"
    chunk_count = 0
    text = ""

    # 2026-07-16 no-PDF honesty: split the scope by what each paper can be summarized FROM. Only
    # full-text papers feed the per-paper map/reduce; abstract-only and title-only papers are each
    # folded into a single shared paragraph so they are acknowledged, not silently dropped.
    full_text_works: list[Work] = []
    abstract_only_works: list[Work] = []
    title_only_works: list[Work] = []
    for w in works:
        tier = _source_tier(_work_source(db, w)[1])
        bucket = (
            full_text_works
            if tier == "full_text"
            else abstract_only_works
            if tier == "abstract_only"
            else title_only_works
        )
        bucket.append(w)
    breakdown = {
        "full_text": len(full_text_works),
        "abstract_only": len(abstract_only_works),
        "title_only": len(title_only_works),
    }

    if summary_type == "local_llm":
        stored_model = model_name or ai_cfg.summary_model
        prompt_version = LLM_PROMPT_VERSION
        # Map step (UX batch 4): build one digest line per FULL-TEXT paper. When the LLM is on,
        # per-paper summaries are generated through summarize_work — so they are also PERSISTED and
        # show up in each paper's own view — and reused on the next scope run. A single LLM outage
        # stops further attempts (cheap digests for the rest) instead of stacking N timeouts.
        digests: list[str] = []
        llm_ok = ai_cfg.summary_provider == "local_llm"
        if not llm_ok:
            fallback_reason = "the local LLM is not enabled"
        total_steps = len(full_text_works)
        for idx, work in enumerate(full_text_works):
            if cancel_cb is not None and cancel_cb():
                from app.workers.queue import JobCancelled  # noqa: PLC0415 (cycle guard)

                raise JobCancelled(f"cancelled after {idx} of {total_steps} papers")
            if progress_cb is not None:
                progress_cb(idx, total_steps)
            digest = _work_digest(
                db,
                work,
                use_llm=llm_ok and fallback_reason is None,
                model_name=stored_model,
                created_by_user_id=created_by_user_id,
                settings=settings,
                detail=paper_detail,
                regenerate=regenerate_papers,
            )
            if digest is None:
                continue
            digest_text, degraded_reason = digest
            if degraded_reason and fallback_reason is None and llm_ok:
                fallback_reason = degraded_reason
            digests.append(f"- {_work_ref(work)}: {digest_text}")

        # Reduce step: pack the full-text digests into context-window-sized chunks; condense each
        # chunk, then synthesize the collection summary (single chunk skips the middle pass).
        main = ""
        if digests and fallback_reason is None:
            try:
                chunks = _pack_blocks(digests, LLM_INPUT_CHAR_BUDGET)
                chunk_count = len(chunks)
                if len(chunks) == 1:
                    main = _ollama_generate(
                        _scope_final_prompt(
                            scope_type, len(full_text_works), chunks[0], from_parts=False
                        ),
                        model=stored_model,
                        base_url=ai_cfg.ollama_url,
                    )
                else:
                    partials = [
                        _ollama_generate(
                            _scope_chunk_prompt(scope_type, i + 1, len(chunks), chunk),
                            model=stored_model,
                            base_url=ai_cfg.ollama_url,
                        )
                        for i, chunk in enumerate(chunks)
                    ]
                    final_notes = "\n\n".join(partials)[:LLM_INPUT_CHAR_BUDGET]
                    main = _ollama_generate(
                        _scope_final_prompt(
                            scope_type, len(full_text_works), final_notes, from_parts=True
                        ),
                        model=stored_model,
                        base_url=ai_cfg.ollama_url,
                    )
                method = "map_reduce"
            except Exception as exc:  # noqa: BLE001 - degrade to extractive, never fail the request
                logger.warning("local_llm scope summary unavailable (%s); extractive fallback", exc)
                fallback_reason = str(exc) or "the local LLM is unavailable"
        if digests and not main:
            # Extractive fallback over the per-paper digests (still better than raw abstracts).
            main = summarize_extractive(
                " ".join(d.lstrip("- ") for d in digests), max_sentences=max_sentences
            )
            provider_used = "extractive"
            # Keep stored_model = the REQUESTED model (like summarize_work), NOT a tier1 name — so the
            # effort×model read still finds this (degraded) summary. provider_used records the
            # degradation honestly (2026-07-16 fix: a degraded scope summary was stored under an
            # extractive model name and the frontend's (effort, configured-model) read 404'd → the
            # window looked empty even though the job "finished").

        use_group_llm = llm_ok and fallback_reason is None
        parts = [main] if main else []
        if abstract_only_works:
            parts.append(
                _abstract_only_paragraph(
                    abstract_only_works,
                    use_llm=use_group_llm,
                    model=stored_model,
                    base_url=ai_cfg.ollama_url,
                )
            )
        if title_only_works:
            parts.append(_title_only_paragraph(title_only_works))
        text = "\n\n".join(p for p in parts if p and p.strip())
        # Honest provenance: when the LLM never ran (disabled) or degraded mid-run, the summary is
        # extractive even if there were no full-text digests to trip the inline fallback above. Keep
        # stored_model = the requested model so the (effort, model) read still finds it (see above).
        if not llm_ok or fallback_reason is not None:
            provider_used = "extractive"
            if not stored_model:  # only when no model was requested/configured at all
                stored_model = "tier1-extractive-frequency-scope"
        if not text:
            raise ValueError(f"No text available in {scope_type!r} scope to summarize")
    else:
        parts = [summarize_extractive(combined, max_sentences=max_sentences)]
        if title_only_works:
            parts.append(_title_only_paragraph(title_only_works))
        text = "\n\n".join(p for p in parts if p and p.strip())
        stored_model = "tier1-extractive-frequency-scope"

    # Replace the same (scope, effort, MODEL) combo; other efforts/models coexist (the cache
    # matrix). LRU-evict models beyond the cap per (scope, effort).
    db.execute(
        delete(Summary).where(
            Summary.entity_type == scope_type,
            Summary.entity_id == entity_id,
            Summary.summary_type == stored_type,
            Summary.model_name == stored_model,
        )
    )
    fallback = provider_used != provider_requested
    summary = Summary(
        entity_type=scope_type,
        entity_id=entity_id,
        summary_type=stored_type,
        text=text,
        model_name=stored_model,
        prompt_version=prompt_version,
        provider_requested=provider_requested,
        provider_used=provider_used,
        fallback=fallback,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        created_by_user_id=created_by_user_id,
        params={
            "scope_type": scope_type,
            "scope_id": str(scope_id) if scope_id is not None else None,
            "summary_type": summary_type,
            "max_sentences": max_sentences,
            "model_name": model_name,
            # Read back by GET /ai/summaries/latest (S15 async completion).
            "work_count": len(works),
            # UX batch 4: which scope this summarizes (shown as the summary's title) + how.
            "scope_label": _scope_label(db, scope_type, scope_id),
            "method": method,
            "chunks": chunk_count,
            "paper_detail": scope_detail,
            # 2026-07-16 no-PDF honesty: how the scope broke down by available source, so the
            # footer can show "N with PDFs, M abstract-only, K title-only".
            "source_breakdown": breakdown,
        },
    )
    db.add(summary)
    db.flush()
    _evict_stale_models(db, scope_type, entity_id, stored_type)
    # A ``fallback_reason`` is transient (not a column): shown only on a fresh degrade.
    summary.fallback_reason = fallback_reason if fallback else None
    return summary, len(works)
