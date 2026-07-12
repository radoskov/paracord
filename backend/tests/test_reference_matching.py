"""Reference→library matching — the "likely local" matcher (batch 12, Phase 2)."""

import uuid

from app.core.config import get_settings
from app.models.citation import Reference
from app.models.metadata import MetadataAssertion
from app.models.work import Work
from app.services.reference_matching import (
    find_reference_match,
    rescan_references_for_new_work,
    resolve_and_persist,
)
from app.utils.normalization import normalize_title

# The motivating pair: identical paper, titles differ only by dash-vs-colon + case.
LOCAL_TITLE = "KnowRob: A knowledge processing infrastructure for cognition-enabled robots"
CITED_TITLE = "KnowRob – A Knowledge Processing Infrastructure for Cognition-enabled Robots"


def _work(db, title, **fields) -> Work:
    work = Work(
        canonical_title=title,
        normalized_title=normalize_title(title or ""),
        **fields,
    )
    db.add(work)
    db.flush()
    return work


def _ref(db, title=None, **fields) -> Reference:
    reference = Reference(
        title=title,
        normalized_title=normalize_title(title) if title else None,
        **fields,
    )
    db.add(reference)
    db.flush()
    return reference


def _set_authors(db, work, names: list[str]) -> None:
    db.add(
        MetadataAssertion(
            entity_type="work",
            entity_id=work.id,
            field_name="authors",
            value="; ".join(names),
            source="test",
            selected_as_canonical=True,
        )
    )
    db.flush()


# --- identifier gate (D2) -------------------------------------------------------------------------


def test_identifier_doi_exact_match_is_local(db) -> None:
    work = _work(db, "Some Paper", doi="10.1/abc", year=2020)
    ref = _ref(db, title="Totally Different Title", doi="10.1/abc")
    changed = resolve_and_persist(db, ref)
    assert changed
    assert ref.resolution_status == "local_match"
    assert ref.resolved_work_id == work.id


def test_conflicting_doi_disqualifies_even_with_matching_title(db) -> None:
    # Same title, but both sides carry a DOI and they differ → NOT a match (D2), no fuzzy fallback.
    _work(db, LOCAL_TITLE, doi="10.1/work", year=2015)
    ref = _ref(db, title=CITED_TITLE, doi="10.1/other", year=2015)
    assert find_reference_match(db, ref) is None
    resolve_and_persist(db, ref)
    assert ref.resolution_status == "external"


def test_arxiv_identifier_match_is_local(db) -> None:
    work = _work(db, "A Paper", arxiv_id="2101.00001", arxiv_base_id="2101.00001", year=2021)
    ref = _ref(db, title="A Paper (preprint)", arxiv_id="arXiv:2101.00001v2")
    resolve_and_persist(db, ref)
    assert ref.resolution_status == "local_match"
    assert ref.resolved_work_id == work.id


# --- fuzzy title + gates --------------------------------------------------------------------------


def test_fuzzy_dash_colon_title_is_likely_match_by_default(db) -> None:
    work = _work(db, LOCAL_TITLE, year=2010)
    ref = _ref(db, title=CITED_TITLE, year=2010)
    match = find_reference_match(db, ref)
    assert match is not None and match.via == "fuzzy"
    assert match.score >= 90.0
    resolve_and_persist(db, ref)  # toggle OFF (default)
    assert ref.resolution_status == "likely_match"
    assert ref.suggested_work_id == work.id
    assert ref.resolved_work_id is None  # a soft guess never lands in resolved_work_id


def test_fuzzy_as_confirmed_promotes_to_local(db) -> None:
    work = _work(db, LOCAL_TITLE, year=2010)
    ref = _ref(db, title=CITED_TITLE, year=2010)
    resolve_and_persist(db, ref, fuzzy_as_confirmed=True)
    assert ref.resolution_status == "local_match"
    assert ref.resolved_work_id == work.id


def test_title_below_threshold_is_external(db) -> None:
    _work(db, "Deep learning for medical imaging", year=2019)
    ref = _ref(db, title="Deep reinforcement learning for robot control", year=2019)
    assert find_reference_match(db, ref) is None
    resolve_and_persist(db, ref)
    assert ref.resolution_status == "external"


def test_year_gate_rejects_when_both_present_and_differ(db) -> None:
    _work(db, LOCAL_TITLE, year=2010)
    ref = _ref(db, title=CITED_TITLE, year=1999)
    assert find_reference_match(db, ref) is None


def test_year_gate_ignored_when_one_side_missing(db) -> None:
    work = _work(db, LOCAL_TITLE, year=2010)
    ref = _ref(db, title=CITED_TITLE, year=None)
    match = find_reference_match(db, ref)
    assert match is not None and match.work_id == work.id


def test_author_gate_disqualifies_disjoint_authors(db) -> None:
    work = _work(db, LOCAL_TITLE, year=2010)
    _set_authors(db, work, ["Alice Aardvark", "Bob Beaver"])
    ref = _ref(db, title=CITED_TITLE, year=2010, authors=["Zeno Zebra", "Yuri Yak"])
    assert find_reference_match(db, ref) is None


def test_author_gate_skipped_when_reference_has_no_authors(db) -> None:
    work = _work(db, LOCAL_TITLE, year=2010)
    _set_authors(db, work, ["Alice Aardvark"])
    ref = _ref(db, title=CITED_TITLE, year=2010, authors=None)  # can't compute → can't disqualify
    match = find_reference_match(db, ref)
    assert match is not None and match.work_id == work.id


def test_author_gate_passes_on_overlap(db) -> None:
    work = _work(db, LOCAL_TITLE, year=2010)
    _set_authors(db, work, ["Moritz Tenorth", "Michael Beetz"])
    ref = _ref(db, title=CITED_TITLE, year=2010, authors=["Tenorth, M.", "Beetz, M."])
    match = find_reference_match(db, ref)
    assert match is not None and match.work_id == work.id


# --- status locks (item #4) -----------------------------------------------------------------------


def test_confirmed_match_is_locked(db) -> None:
    other = _work(db, "Unrelated", year=2000)
    _work(db, LOCAL_TITLE, year=2010)
    ref = _ref(
        db,
        title=CITED_TITLE,
        year=2010,
        resolution_status="confirmed_match",
        resolved_work_id=other.id,
    )
    assert not resolve_and_persist(db, ref)
    assert ref.resolution_status == "confirmed_match"
    assert ref.resolved_work_id == other.id  # untouched


def test_rejected_candidate_is_not_re_proposed(db) -> None:
    work = _work(db, LOCAL_TITLE, year=2010)
    ref = _ref(
        db,
        title=CITED_TITLE,
        year=2010,
        resolution_status="rejected_match",
        suggested_work_id=work.id,
    )
    resolve_and_persist(db, ref)
    assert ref.resolution_status == "rejected_match"  # stays rejected — same candidate
    assert ref.resolved_work_id is None


def test_rejected_still_surfaces_a_different_better_candidate(db) -> None:
    rejected = _work(db, "KnowRob knowledge base tools", year=2010)
    better = _work(db, LOCAL_TITLE, year=2010)
    ref = _ref(
        db,
        title=CITED_TITLE,
        year=2010,
        resolution_status="rejected_match",
        suggested_work_id=rejected.id,
    )
    resolve_and_persist(db, ref)
    assert ref.resolution_status == "likely_match"
    assert ref.suggested_work_id == better.id


# --- reverse rescan on a new work -----------------------------------------------------------------


def test_reverse_rescan_links_external_reference_to_new_work(db) -> None:
    # An external reference exists first; the work it cites arrives later.
    ref = _ref(db, title=CITED_TITLE, doi="10.5/knowrob", year=2010, resolution_status="external")
    new_work = _work(db, LOCAL_TITLE, doi="10.5/knowrob", year=2010)
    changed = rescan_references_for_new_work(db, new_work)
    assert changed == 1
    assert ref.resolution_status == "local_match"  # same in-session object the matcher mutated
    assert ref.resolved_work_id == new_work.id


def test_reverse_rescan_ignores_confirmed_and_unrelated(db) -> None:
    confirmed = _ref(
        db,
        title=CITED_TITLE,
        year=2010,
        resolution_status="confirmed_match",
        resolved_work_id=uuid.uuid4(),
    )
    prior = confirmed.resolved_work_id
    new_work = _work(db, LOCAL_TITLE, year=2010)
    rescan_references_for_new_work(db, new_work)
    db.refresh(confirmed)
    assert confirmed.resolved_work_id == prior  # locked


def test_disabled_matcher_does_nothing(db, monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "reference_matching_enabled", False)
    _work(db, LOCAL_TITLE, doi="10.1/x", year=2010)
    ref = _ref(db, title=CITED_TITLE, doi="10.1/x", year=2010)
    assert find_reference_match(db, ref, settings=settings) is None


# --- endpoints (D3 rescan) ------------------------------------------------------------------------


def test_per_paper_rescan_endpoint_links_reference(client, auth_headers, db, make_reference) -> None:
    citing = _work(db, "Citing Paper", year=2021)
    target = _work(db, LOCAL_TITLE, year=2010)
    make_reference(db, citing_work_id=citing.id, title=CITED_TITLE, year=2010)
    db.commit()

    r = client.post(
        f"/api/v1/works/{citing.id}/references/rescan", headers=auth_headers("editor")
    )
    assert r.status_code == 200
    body = r.json()
    assert body["scanned"] == 1 and body["changed"] == 1

    refs = client.get(
        f"/api/v1/works/{citing.id}/references", headers=auth_headers("editor")
    ).json()
    assert refs[0]["resolution_status"] == "likely_match"
    assert refs[0]["suggested_work_id"] == str(target.id)


def test_library_wide_rescan_enqueues_and_audits(client, auth_headers, db, monkeypatch) -> None:
    from app.models.audit import AuditEvent
    from app.workers import queue as queue_mod

    monkeypatch.setattr(queue_mod, "enqueue_reference_rescan", lambda: "job-xyz")
    r = client.post("/api/v1/works/references/rescan-all", headers=auth_headers("editor"))
    assert r.status_code == 200
    body = r.json()
    assert body["queued"] is True and body["job_id"] == "job-xyz"

    from sqlalchemy import select

    events = db.scalars(
        select(AuditEvent).where(AuditEvent.event_type == "reference.rescan_all")
    ).all()
    assert len(events) == 1


def test_per_paper_rescan_requires_contributor(client, auth_headers, db, make_reference) -> None:
    citing = _work(db, "Citing Paper", year=2021)
    db.commit()
    r = client.post(
        f"/api/v1/works/{citing.id}/references/rescan", headers=auth_headers("reader")
    )
    assert r.status_code == 403
