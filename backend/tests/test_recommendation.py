"""AI recommendation service — pure scoring math + orchestration with a fake ranker.

The LLM/embedding calls are injected (a deterministic fake Ranker), so these assert the combine
algorithm (rank points + 0.5 hierarchy propagation), the affinity/rank fallback, JSON parsing, and
the end-to-end per-paper result shape — without any model."""

from pathlib import Path

import pytest
from app.core.security import Role
from app.db.base import Base
from app.models.organization import (
    Rack,
    RackShelf,
    Row,
    RowRack,
    Shelf,
    ShelfWork,
    Tag,
    TagLink,
    TagRack,
    TagRow,
    TagShelf,
)
from app.models.user import User
from app.models.work import Work
from app.services import recommendation as rec
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.slow


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'rec.db'}")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            Work.__table__,
            Shelf.__table__,
            Rack.__table__,
            Row.__table__,
            ShelfWork.__table__,
            RackShelf.__table__,
            RowRack.__table__,
            Tag.__table__,
            TagLink.__table__,
            TagShelf.__table__,
            TagRack.__table__,
            TagRow.__table__,
            User.__table__,
        ],
    )
    with sessionmaker(bind=engine)() as session:
        yield session


class _FakeRanker:
    """Deterministic ranker: picks candidates in the given order (best first), affinity 90,80,…"""

    def __init__(self, provides_affinity: bool = True):
        self.provides_affinity = provides_affinity

    def rank(self, feature_block, candidates, k):
        picks = [
            {"index": i, "affinity": (90 - 10 * i) if self.provides_affinity else None}
            for i in range(min(k, len(candidates)))
        ]
        return rec.RankResult(picks=picks, raw_input="IN", raw_output="OUT")


def _bundle(kinds, *, affinity=True, no_llm=False):
    return rec.RankerBundle(
        by_kind={k: _FakeRanker(provides_affinity=affinity) for k in kinds},
        embed=lambda _t: [1.0, 0.0],
        provider_used="fake",
        no_llm_fallback=no_llm,
    )


# --- pure scoring ---------------------------------------------------------------------------------
def test_rank_points_and_combine() -> None:
    assert rec.rank_points(1, 5) == 5.0 and rec.rank_points(5, 5) == 1.0
    assert rec.rank_points(6, 5) == 0.0  # never negative
    assert rec.combine([2, 1], "sum") == 3.0
    assert rec.combine([4, 1, 1], "median") == 1.0
    assert rec.combine([4, 1, 1], "max") == 4.0
    assert rec.combine([], "sum") == 0.0


def test_combine_categorization_propagates_down_the_hierarchy() -> None:
    # rows A,B (base 2,1); racks A,B (base 2,1); shelves A,B (base 2,1); A-chain, B-chain.
    per_kind = {
        "row": [rec.Pick("rowA", "Row A", 1, None, 2.0), rec.Pick("rowB", "Row B", 2, None, 1.0)],
        "rack": [rec.Pick("rkA", "Rack A", 1, None, 2.0), rec.Pick("rkB", "Rack B", 2, None, 1.0)],
        "shelf": [
            rec.Pick("shA", "Shelf A", 1, None, 2.0),
            rec.Pick("shB", "Shelf B", 2, None, 1.0),
        ],
    }
    shelves, rack_final = rec.combine_categorization(
        per_kind,
        rack_parent_rows={"rkA": ["rowA"], "rkB": ["rowB"]},
        shelf_parent_racks={"shA": ["rkA"], "shB": ["rkB"]},
        parent_combine="sum",
    )
    # rackA final = 2 + 0.5*2 = 3; shelfA = 2 + 0.5*3 = 3.5; shelfB = 1 + 0.5*(1+0.5*1)=1.75
    assert rack_final["rkA"] == 3.0
    assert shelves[0]["shelf_id"] == "shA" and shelves[0]["score"] == 3.5
    assert shelves[1]["shelf_id"] == "shB" and shelves[1]["score"] == 1.75


def test_picks_from_rank_affinity_and_fallback() -> None:
    cands = [rec.Candidate("a", "A"), rec.Candidate("b", "B")]
    res = rec.RankResult(
        picks=[{"index": 0, "affinity": 88}, {"index": 1, "affinity": 70}],
        raw_input="",
        raw_output="",
    )
    picks, missing = rec.picks_from_rank(res, cands, k=2, scoring="affinity")
    assert [p.base for p in picks] == [88.0, 70.0] and not missing
    # affinity requested but none returned → base falls back to rank points, flagged.
    res2 = rec.RankResult(picks=[{"index": 0, "affinity": None}], raw_input="", raw_output="")
    picks2, missing2 = rec.picks_from_rank(res2, cands, k=2, scoring="affinity")
    assert picks2[0].base == 2.0 and missing2


def test_extract_json_tolerates_prose() -> None:
    assert rec._extract_json('{"picks": []}') == {"picks": []}
    assert (
        rec._extract_json('here you go: {"picks": [{"index": 1}]} thanks')["picks"][0]["index"] == 1
    )
    assert rec._extract_json("not json at all") is None


# --- orchestration (fake ranker) ------------------------------------------------------------------
def _owner(db) -> User:
    u = User(username="owner", password_hash="x", role=Role.OWNER)
    db.add(u)
    db.flush()
    return u


def test_run_recommendation_categorization_end_to_end(db_session) -> None:
    owner = _owner(db_session)
    work = Work(canonical_title="Paper", normalized_title="paper", abstract="graphs")
    rowA, rowB = Row(name="Row A"), Row(name="Row B")
    rkA, rkB = Rack(name="Rack A"), Rack(name="Rack B")
    shA, shB = Shelf(name="Shelf A"), Shelf(name="Shelf B")
    db_session.add_all([work, rowA, rowB, rkA, rkB, shA, shB])
    db_session.flush()
    db_session.add_all(
        [RowRack(row_id=rowA.id, rack_id=rkA.id), RowRack(row_id=rowB.id, rack_id=rkB.id)]
    )
    db_session.add_all(
        [RackShelf(rack_id=rkA.id, shelf_id=shA.id), RackShelf(rack_id=rkB.id, shelf_id=shB.id)]
    )
    db_session.commit()

    out = rec.run_recommendation(
        db_session,
        works=[work],
        mode="categorization",
        k=2,
        scoring="ranking",
        parent_combine="sum",
        prefilter=False,
        actor=owner,
        bundle=_bundle(list(rec.CATEGORY_KINDS)),
    )
    assert out["mode"] == "categorization" and len(out["papers"]) == 1
    shelves = out["papers"][0]["shelves"]
    # Shelf A wins via the A-chain (Row A → Rack A → Shelf A), matching the pure-math test.
    assert shelves[0]["name"] == "Shelf A" and shelves[0]["score"] == 3.5
    assert out["papers"][0]["raw"]["shelf"]["output"] == "OUT"  # raw LLM I/O captured for the popup


def test_run_recommendation_tags_excludes_applied_and_flags_no_llm(db_session) -> None:
    owner = _owner(db_session)
    work = Work(canonical_title="P", normalized_title="p")
    keep, applied = (
        Tag(name="keep", normalized_name="keep"),
        Tag(name="applied", normalized_name="applied"),
    )
    db_session.add_all([work, keep, applied])
    db_session.flush()
    db_session.add(TagLink(tag_id=applied.id, entity_type="work", entity_id=work.id))
    db_session.commit()

    out = rec.run_recommendation(
        db_session,
        works=[work],
        mode="tags",
        k=5,
        scoring="ranking",
        parent_combine="sum",
        prefilter=False,
        actor=owner,
        bundle=_bundle(["tag"], no_llm=True),
    )
    names = [s["name"] for s in out["papers"][0]["suggestions"]]
    assert "keep" in names and "applied" not in names  # already-applied tag is not re-suggested
    assert out["fallback"] is True  # no-LLM bundle → embedding-fallback flag surfaced
