"""AI "Recommend categorization" (Insights → Recommend categorization).

For a scope of papers, recommend either TAGS or CATEGORIES (rows/racks/shelves) per paper from the
paper's *features* (title, abstract, keywords, topics), which the user then reviews and accepts.

Design (owner decisions, workplan Part C):
- Each paper is processed independently; for categorization each kind (row/rack/shelf) is ranked
  independently, then combined down the hierarchy (a rack gains 0.5× its picked parent-rows' scores;
  a shelf gains 0.5× its picked parent-racks' combined scores). The multi-parent combine is a
  pre-run choice: sum | median | max.
- Base points per pick p (1=best) = ``K − p + 1``; OR the LLM's 0–100 affinity when ``scoring ==
  "affinity"`` and the LLM returns usable numbers (else fall back to rank points, flagged).
- No generative LLM configured → degrade to embedding-cosine ranking (no affinity), flagged.
- The scoring math here is pure + unit-tested; the LLM/embedding calls go through injectable
  ``Ranker`` objects so tests use fakes.
"""

from __future__ import annotations

import json
import re
import statistics
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.organization import (
    RackShelf,
    RowRack,
    ShelfWork,
    Tag,
    TagLink,
    TagRack,
    TagRow,
    TagShelf,
)
from app.models.user import User
from app.models.work import Work
from app.services import access

# Keep the candidate list + feature block well within the LLM input budget (mirrors summarization).
_FEATURE_CHAR_BUDGET = 4000
_MAX_CANDIDATES_IN_PROMPT = 60  # hard cap; the embedding prefilter shrinks below this when enabled
CATEGORY_KINDS = ("row", "rack", "shelf")


# --------------------------------------------------------------------------------------------------
# Candidate + result shapes (all JSON-serialisable dicts in the stored result)
# --------------------------------------------------------------------------------------------------
@dataclass
class Candidate:
    id: str
    name: str
    description: str = ""


@dataclass
class Pick:
    """One ranked recommendation for a paper: the candidate + its rank (1=best) and base score."""

    id: str
    name: str
    rank: int
    affinity: float | None  # 0–100 from the LLM, or None (rank-only)
    base: float  # the base points that feed combination/final ranking


@dataclass
class RankResult:
    picks: list[dict[str, Any]]  # [{index:int, affinity:float|None}] best-first
    raw_input: str
    raw_output: str


class Ranker(Protocol):
    """Ranks candidates for a paper. Returns the top picks best-first + raw I/O for the UI popup."""

    provides_affinity: bool

    def rank(self, feature_block: str, candidates: list[Candidate], k: int) -> RankResult: ...


# --------------------------------------------------------------------------------------------------
# Paper features
# --------------------------------------------------------------------------------------------------
def paper_features(work: Work) -> dict[str, Any]:
    """The features a paper contributes to a recommendation (title/abstract/keywords/topics)."""
    return {
        "title": work.canonical_title or "",
        "abstract": work.abstract or "",
        "keywords": list(work.keywords or []),
        "topics": list(work.topics or []),
    }


def feature_block(features: dict[str, Any]) -> str:
    """A compact, budget-bounded text block describing a paper for the prompt / embedding."""
    parts = [f"Title: {features['title']}".strip()]
    if features.get("keywords"):
        parts.append("Keywords: " + ", ".join(map(str, features["keywords"][:20])))
    if features.get("topics"):
        parts.append("Topics: " + ", ".join(map(str, features["topics"][:20])))
    if features.get("abstract"):
        parts.append("Abstract: " + features["abstract"])
    return "\n".join(parts)[:_FEATURE_CHAR_BUDGET]


# --------------------------------------------------------------------------------------------------
# Pure scoring
# --------------------------------------------------------------------------------------------------
def rank_points(rank: int, k: int) -> float:
    """Base points for a pick at position ``rank`` (1=best) among K: ``K − rank + 1`` (min 0)."""
    return float(max(0, k - rank + 1))


def combine(values: list[float], mode: str) -> float:
    """Combine several parent scores into one contribution (before the 0.5 factor)."""
    if not values:
        return 0.0
    if mode == "sum":
        return float(sum(values))
    if mode == "median":
        return float(statistics.median(values))
    if mode == "max":
        return float(max(values))
    raise ValueError(f"Unknown parent-combine mode: {mode!r}")


# Propagation factor: a child gains this fraction of its picked parents' combined score.
PARENT_FACTOR = 0.5


def combine_categorization(
    per_kind: dict[str, list[Pick]],
    *,
    rack_parent_rows: dict[str, list[str]],
    shelf_parent_racks: dict[str, list[str]],
    parent_combine: str,
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    """Combine independent per-kind picks into a final shelf ranking (workplan C5).

    ``rack_parent_rows`` maps rack_id → its row_ids; ``shelf_parent_racks`` maps shelf_id → its
    rack_ids. A rack's final score = its base + 0.5×combine(picked parent rows' base); a shelf's
    final = its base + 0.5×combine(picked parent racks' *final* scores). Returns
    ``(ranked_shelves, rack_final_by_id)`` — the shelves sorted by final score desc, each carrying a
    breakdown for the UI popup.
    """
    row_base = {p.id: p.base for p in per_kind.get("row", [])}
    rack_picks = {p.id: p for p in per_kind.get("rack", [])}
    shelf_picks = {p.id: p for p in per_kind.get("shelf", [])}

    rack_final: dict[str, float] = {}
    rack_boost: dict[str, float] = {}
    for rid, rp in rack_picks.items():
        parent_scores = [row_base[r] for r in rack_parent_rows.get(rid, []) if r in row_base]
        boost = PARENT_FACTOR * combine(parent_scores, parent_combine)
        rack_boost[rid] = boost
        rack_final[rid] = rp.base + boost

    ranked: list[dict[str, Any]] = []
    for sid, sp in shelf_picks.items():
        parent_scores = [rack_final[r] for r in shelf_parent_racks.get(sid, []) if r in rack_final]
        boost = PARENT_FACTOR * combine(parent_scores, parent_combine)
        final = sp.base + boost
        ranked.append(
            {
                "shelf_id": sid,
                "name": sp.name,
                "score": round(final, 4),
                "rank": sp.rank,
                "affinity": sp.affinity,
                "base": round(sp.base, 4),
                "parent_boost": round(boost, 4),
            }
        )
    ranked.sort(key=lambda x: x["score"], reverse=True)
    return ranked, rack_final


# --------------------------------------------------------------------------------------------------
# Candidate collection (access-filtered)
# --------------------------------------------------------------------------------------------------
def _as_candidates(objs: Iterable[Any]) -> list[Candidate]:
    return [Candidate(id=str(o.id), name=o.name, description=o.description or "") for o in objs]


def visible_category_candidates(db: Session, actor: User, kind: str) -> list[Candidate]:
    """All rows/racks/shelves the actor may SEE, as ranking candidates (name + description)."""
    q = {
        "row": access.visible_rows_query,
        "rack": access.visible_racks_query,
        "shelf": access.visible_shelves_query,
    }[kind](db, actor)
    return _as_candidates(db.scalars(q).all())


def assignable_tag_candidates(db: Session, actor: User, work_id: uuid.UUID) -> list[Candidate]:
    """Tags offered for a paper (global + scoped to its shelves/racks/rows) that are NOT already
    applied — the candidate set for tag recommendation. Mirrors ``/tags/assignable``."""
    shelf_ids = set(
        db.scalars(select(ShelfWork.shelf_id).where(ShelfWork.work_id == work_id)).all()
    )
    rack_ids = (
        set(db.scalars(select(RackShelf.rack_id).where(RackShelf.shelf_id.in_(shelf_ids))).all())
        if shelf_ids
        else set()
    )
    row_ids = (
        set(db.scalars(select(RowRack.row_id).where(RowRack.rack_id.in_(rack_ids))).all())
        if rack_ids
        else set()
    )
    scoped = (
        set(db.scalars(select(TagShelf.tag_id)).all())
        | set(db.scalars(select(TagRack.tag_id)).all())
        | set(db.scalars(select(TagRow.tag_id)).all())
    )
    matching: set[uuid.UUID] = set()
    if shelf_ids:
        matching |= set(
            db.scalars(select(TagShelf.tag_id).where(TagShelf.shelf_id.in_(shelf_ids))).all()
        )
    if rack_ids:
        matching |= set(
            db.scalars(select(TagRack.tag_id).where(TagRack.rack_id.in_(rack_ids))).all()
        )
    if row_ids:
        matching |= set(db.scalars(select(TagRow.tag_id).where(TagRow.row_id.in_(row_ids))).all())
    applied = set(
        db.scalars(
            select(TagLink.tag_id).where(
                TagLink.entity_type == "work", TagLink.entity_id == work_id
            )
        ).all()
    )
    tags = db.scalars(select(Tag).order_by(Tag.name)).all()
    offered = [t for t in tags if (t.id not in scoped or t.id in matching) and t.id not in applied]
    return _as_candidates(offered)


def category_parent_maps(
    db: Session, actor: User
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """(rack_id→its visible row_ids, shelf_id→its visible rack_ids) for score propagation."""
    visible_rows = {
        str(r) for r in db.scalars(select(access.visible_rows_query(db, actor).subquery().c.id))
    }
    visible_racks = {
        str(r) for r in db.scalars(select(access.visible_racks_query(db, actor).subquery().c.id))
    }
    rack_parent_rows: dict[str, list[str]] = {}
    for row_id, rack_id in db.execute(select(RowRack.row_id, RowRack.rack_id)).all():
        if str(row_id) in visible_rows:
            rack_parent_rows.setdefault(str(rack_id), []).append(str(row_id))
    shelf_parent_racks: dict[str, list[str]] = {}
    for rack_id, shelf_id in db.execute(select(RackShelf.rack_id, RackShelf.shelf_id)).all():
        if str(rack_id) in visible_racks:
            shelf_parent_racks.setdefault(str(shelf_id), []).append(str(rack_id))
    return rack_parent_rows, shelf_parent_racks


# --------------------------------------------------------------------------------------------------
# Turning a Ranker result into scored Picks
# --------------------------------------------------------------------------------------------------
def picks_from_rank(
    result: RankResult, candidates: list[Candidate], k: int, scoring: str
) -> tuple[list[Pick], bool]:
    """Map a ranker's index-picks to scored :class:`Pick`s. Returns (picks, affinity_missing).

    ``affinity_missing`` is True when ``scoring=='affinity'`` but the ranker gave no usable numbers
    (so the caller flags a fallback to rank points)."""
    picks: list[Pick] = []
    want_affinity = scoring == "affinity"
    any_affinity = False
    for rank, entry in enumerate(result.picks[:k], start=1):
        idx = entry.get("index")
        if idx is None or not (0 <= idx < len(candidates)):
            continue
        cand = candidates[idx]
        aff = entry.get("affinity")
        aff = float(aff) if isinstance(aff, (int, float)) else None
        if aff is not None:
            any_affinity = True
        base = aff if (want_affinity and aff is not None) else rank_points(rank, k)
        picks.append(Pick(id=cand.id, name=cand.name, rank=rank, affinity=aff, base=float(base)))
    affinity_missing = want_affinity and not any_affinity
    # If affinity was requested but none came back, re-base on rank points (already done above per
    # pick since aff is None), so nothing else to do — just report the flag.
    return picks, affinity_missing


# --------------------------------------------------------------------------------------------------
# Rankers (LLM JSON + embedding cosine); injectable for tests
# --------------------------------------------------------------------------------------------------
def _extract_json(text: str) -> Any:
    """Parse JSON from an LLM response, tolerating prose around it (free-text-parse fallback)."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    m = re.search(r"[\[{].*[\]}]", text or "", re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


def ollama_generate_json(prompt: str, *, model: str, base_url: str) -> tuple[Any, str]:
    """Ollama generation asking for JSON output (C7). Returns (parsed_or_None, raw_text)."""
    import httpx2 as httpx

    with httpx.Client(timeout=120) as client:
        response = client.post(
            f"{base_url.rstrip('/')}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False, "format": "json"},
        )
        response.raise_for_status()
        raw = (response.json().get("response") or "").strip()
    return _extract_json(raw), raw


def _rank_prompt(feature_block_text: str, candidates: list[Candidate], k: int, kind: str) -> str:
    lines = [
        f"You are categorising an academic paper into the best-matching {kind}s from a fixed list.",
        f"Choose up to {k} {kind}s that best characterise the paper, best first.",
        'Return ONLY JSON: {"picks": [{"index": <int>, "affinity": <0-100>}]}, where index '
        f"refers to the numbered {kind} list and affinity is your confidence (0-100).",
        "",
        "PAPER:",
        feature_block_text,
        "",
        f"{kind.upper()}S (index: name — description):",
    ]
    for i, c in enumerate(candidates):
        desc = f" — {c.description}" if c.description else ""
        lines.append(f"{i}: {c.name}{desc}")
    return "\n".join(lines)[:11000]


@dataclass
class OllamaRanker:
    model: str
    base_url: str
    kind: str
    provides_affinity: bool = True

    def rank(self, feature_block_text: str, candidates: list[Candidate], k: int) -> RankResult:
        prompt = _rank_prompt(feature_block_text, candidates, k, self.kind)
        parsed, raw = ollama_generate_json(prompt, model=self.model, base_url=self.base_url)
        picks: list[dict[str, Any]] = []
        if isinstance(parsed, dict) and isinstance(parsed.get("picks"), list):
            picks = [p for p in parsed["picks"] if isinstance(p, dict)]
        elif isinstance(parsed, list):  # tolerate a bare list of picks
            picks = [p for p in parsed if isinstance(p, dict)]
        return RankResult(picks=picks, raw_input=prompt, raw_output=raw)


@dataclass
class EmbeddingRanker:
    """No-LLM fallback: rank candidates by cosine of the paper's vector vs each candidate's
    name+description vector. No affinity (workplan C10)."""

    embed: Callable[[str], list[float]]
    provides_affinity: bool = False

    def rank(self, feature_block_text: str, candidates: list[Candidate], k: int) -> RankResult:
        from app.services.vector_math import dense_cosine

        pv = self.embed(feature_block_text)
        scored = []
        for i, c in enumerate(candidates):
            cv = self.embed(f"{c.name}. {c.description}".strip())
            scored.append((i, dense_cosine(pv, cv)))
        scored.sort(key=lambda x: x[1], reverse=True)
        picks = [{"index": i, "affinity": None} for i, _ in scored[:k]]
        return RankResult(picks=picks, raw_input="(embedding cosine ranking)", raw_output="")


# --------------------------------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------------------------------
@dataclass
class RankerBundle:
    """The ranker(s) to use for a run + provenance for the cached result."""

    by_kind: dict[str, Ranker]  # keys: "tag" and/or "row"/"rack"/"shelf"
    embed: Callable[[str], list[float]]
    provider_used: str
    no_llm_fallback: bool  # True when no generative LLM was available → embedding ranking (C10)


def resolve_rankers(db: Session, kinds: list[str]) -> RankerBundle:
    """Pick real rankers from the runtime AI config: the local LLM (JSON) when configured, else an
    embedding-cosine fallback. Also returns an embed fn for the optional prefilter."""
    from app.services.ai_config import get_ai_config
    from app.services.embeddings import resolve_embedding_provider

    cfg = get_ai_config(db)
    resolved = resolve_embedding_provider(db=db)
    embed = resolved.provider.embed
    have_llm = cfg.summary_provider == "local_llm" and bool(cfg.summary_model)
    if have_llm:
        by_kind = {
            k: OllamaRanker(model=cfg.summary_model, base_url=cfg.ollama_url, kind=k) for k in kinds
        }
        return RankerBundle(
            by_kind, embed, provider_used=f"local_llm:{cfg.summary_model}", no_llm_fallback=False
        )
    by_kind = {k: EmbeddingRanker(embed=embed) for k in kinds}
    return RankerBundle(
        by_kind, embed, provider_used=f"embedding:{resolved.requested}", no_llm_fallback=True
    )


def _prefilter(
    candidates: list[Candidate],
    feature_block_text: str,
    embed: Callable[[str], list[float]],
    m: int,
) -> list[Candidate]:
    """Shortlist the top-``m`` candidates by embedding cosine (keeps prompts small + fast)."""
    if len(candidates) <= m:
        return candidates
    from app.services.vector_math import dense_cosine

    pv = embed(feature_block_text)
    scored = [
        (c, dense_cosine(pv, embed(f"{c.name}. {c.description}".strip()))) for c in candidates
    ]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [c for c, _ in scored[:m]]


def _rank_kind(
    ranker: Ranker,
    fb: str,
    candidates: list[Candidate],
    *,
    k: int,
    scoring: str,
    prefilter: bool,
    embed: Callable[[str], list[float]],
) -> tuple[list[Pick], RankResult, bool]:
    cands = candidates
    if prefilter or len(candidates) > _MAX_CANDIDATES_IN_PROMPT:
        cands = _prefilter(candidates, fb, embed, _MAX_CANDIDATES_IN_PROMPT)
    result = ranker.rank(fb, cands, k)
    picks, affinity_missing = picks_from_rank(result, cands, k, scoring)
    return picks, result, affinity_missing


def recommend_work_tags(
    db: Session,
    work: Work,
    *,
    k: int,
    scoring: str,
    prefilter: bool,
    actor: User,
    bundle: RankerBundle,
) -> tuple[dict[str, Any], bool]:
    """Tag recommendation for one paper. Returns (paper_result, affinity_missing)."""
    fb = feature_block(paper_features(work))
    candidates = assignable_tag_candidates(db, actor, work.id)
    if not candidates:
        return {
            "work_id": str(work.id),
            "title": work.canonical_title,
            "suggestions": [],
            "raw": {},
        }, False
    picks, result, affinity_missing = _rank_kind(
        bundle.by_kind["tag"],
        fb,
        candidates,
        k=k,
        scoring=scoring,
        prefilter=prefilter,
        embed=bundle.embed,
    )
    suggestions = [
        {
            "tag_id": p.id,
            "name": p.name,
            "rank": p.rank,
            "affinity": p.affinity,
            "base": round(p.base, 4),
        }
        for p in picks
    ]
    return {
        "work_id": str(work.id),
        "title": work.canonical_title,
        "suggestions": suggestions,
        "raw": {"tag": {"input": result.raw_input, "output": result.raw_output}},
    }, affinity_missing


def recommend_work_categories(
    db: Session,
    work: Work,
    *,
    k: int,
    scoring: str,
    parent_combine: str,
    prefilter: bool,
    actor: User,
    bundle: RankerBundle,
    candidates_by_kind: dict[str, list[Candidate]],
    parents: tuple[dict[str, list[str]], dict[str, list[str]]],
) -> tuple[dict[str, Any], bool]:
    """Categorization for one paper: rank each kind, then combine into a final shelf ranking."""
    fb = feature_block(paper_features(work))
    per_kind: dict[str, list[Pick]] = {}
    raw: dict[str, Any] = {}
    affinity_missing = False
    for kind in CATEGORY_KINDS:
        cands = candidates_by_kind.get(kind, [])
        if not cands:
            per_kind[kind] = []
            continue
        picks, result, miss = _rank_kind(
            bundle.by_kind[kind],
            fb,
            cands,
            k=k,
            scoring=scoring,
            prefilter=prefilter,
            embed=bundle.embed,
        )
        per_kind[kind] = picks
        raw[kind] = {"input": result.raw_input, "output": result.raw_output}
        affinity_missing = affinity_missing or miss
    rack_parent_rows, shelf_parent_racks = parents
    shelves, _rack_final = combine_categorization(
        per_kind,
        rack_parent_rows=rack_parent_rows,
        shelf_parent_racks=shelf_parent_racks,
        parent_combine=parent_combine,
    )
    per_kind_out = {
        kind: [
            {
                "id": p.id,
                "name": p.name,
                "rank": p.rank,
                "affinity": p.affinity,
                "base": round(p.base, 4),
            }
            for p in per_kind.get(kind, [])
        ]
        for kind in CATEGORY_KINDS
    }
    return {
        "work_id": str(work.id),
        "title": work.canonical_title,
        "per_kind": per_kind_out,
        "shelves": shelves,
        "raw": raw,
    }, affinity_missing


def run_recommendation(
    db: Session,
    *,
    works: list[Work],
    mode: str,
    k: int,
    scoring: str,
    parent_combine: str,
    prefilter: bool,
    actor: User,
    bundle: RankerBundle | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Compute a full recommendation over ``works``. Returns the JSON-serialisable result payload
    (papers + provenance). ``bundle`` is injected in tests; otherwise resolved from the AI config."""
    kinds = ["tag"] if mode == "tags" else list(CATEGORY_KINDS)
    bundle = bundle or resolve_rankers(db, kinds)
    fallback = bundle.no_llm_fallback

    # Shared, access-filtered category candidates + parent maps (computed once, not per paper).
    candidates_by_kind: dict[str, list[Candidate]] = {}
    parents: tuple[dict[str, list[str]], dict[str, list[str]]] = ({}, {})
    if mode == "categorization":
        candidates_by_kind = {
            k2: visible_category_candidates(db, actor, k2) for k2 in CATEGORY_KINDS
        }
        parents = category_parent_maps(db, actor)

    papers: list[dict[str, Any]] = []
    total = len(works)
    for i, work in enumerate(works):
        if cancel_cb and cancel_cb():
            break
        if mode == "tags":
            paper, miss = recommend_work_tags(
                db, work, k=k, scoring=scoring, prefilter=prefilter, actor=actor, bundle=bundle
            )
        else:
            paper, miss = recommend_work_categories(
                db,
                work,
                k=k,
                scoring=scoring,
                parent_combine=parent_combine,
                prefilter=prefilter,
                actor=actor,
                bundle=bundle,
                candidates_by_kind=candidates_by_kind,
                parents=parents,
            )
        fallback = fallback or miss
        papers.append(paper)
        if progress_cb:
            progress_cb(i + 1, total)

    return {
        "mode": mode,
        "papers": papers,
        "fallback": fallback,
        "provider_used": bundle.provider_used,
        "affinity_requested": scoring == "affinity",
    }
