"""Reference→library matching — the "likely local" matcher (batch 12, Phase 2)."""

import uuid

from app.core.config import get_settings
from app.models.citation import Reference
from app.models.metadata import MetadataAssertion
from app.models.work import Work
from app.services.reference_matching import (
    AcceptPolicy,
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


def test_fuzzy_dash_colon_title_is_likely_match_below_auto_accept(db, monkeypatch) -> None:
    # Pin the high-confidence threshold out of reach — this test exercises the SOFT likely-match
    # path (at the default threshold of 100 this exact-normalized-title pair auto-confirms).
    monkeypatch.setattr(
        get_settings(), "reference_matching_high_confidence_threshold", 101.0, raising=False
    )
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


def test_perfect_title_match_is_auto_confirmed_without_identifier(db) -> None:
    """UX batch: a 100% fuzzy match (exact normalized title) is hard-linked even with the
    fuzzy-as-confirmed toggle OFF and no DOI/arXiv id on either side."""
    work = _work(db, LOCAL_TITLE, year=2010)
    ref = _ref(db, title=LOCAL_TITLE, year=2010)
    resolve_and_persist(db, ref)
    assert ref.resolution_status == "local_match"
    assert ref.resolved_work_id == work.id


def test_fuzzy_auto_accept_policy_promotes_matches_at_threshold(db) -> None:
    """The admin's fuzzy auto-accept level: score ≥ its threshold hard-links even when the
    high-confidence level is off."""
    policy = AcceptPolicy(
        use_fuzzy=True,
        fuzzy_threshold=90.0,
        use_high_confidence=False,
        high_confidence_threshold=100.0,
    )
    work = _work(db, LOCAL_TITLE, year=2010)
    ref = _ref(db, title=CITED_TITLE, year=2010)
    resolve_and_persist(db, ref, accept_policy=policy)
    assert ref.resolution_status == "local_match"
    assert ref.resolved_work_id == work.id


def test_high_confidence_toggle_off_keeps_perfect_match_likely(db) -> None:
    """Both acceptance levels off → even a 100% title match stays a soft suggestion."""
    policy = AcceptPolicy(
        use_fuzzy=False,
        fuzzy_threshold=90.0,
        use_high_confidence=False,
        high_confidence_threshold=100.0,
    )
    work = _work(db, LOCAL_TITLE, year=2010)
    ref = _ref(db, title=LOCAL_TITLE, year=2010)
    resolve_and_persist(db, ref, accept_policy=policy)
    assert ref.resolution_status == "likely_match"
    assert ref.suggested_work_id == work.id


def test_accept_policy_levels() -> None:
    p = AcceptPolicy(
        use_fuzzy=True,
        fuzzy_threshold=92.0,
        use_high_confidence=True,
        high_confidence_threshold=100.0,
    )
    assert p.accepts(92.0) and p.accepts(100.0)
    assert not p.accepts(91.9)
    only_high = AcceptPolicy(
        use_fuzzy=False,
        fuzzy_threshold=92.0,
        use_high_confidence=True,
        high_confidence_threshold=100.0,
    )
    assert only_high.accepts(100.0)
    assert not only_high.accepts(99.9)


def test_effective_fuzzy_threshold_clamps_to_yaml_floor(db, monkeypatch) -> None:
    """The admin-stored threshold can never take effect below the yaml-only floor."""
    from app.services.app_config import (
        _ensure_row,
        effective_fuzzy_accept_threshold,
        update_fuzzy_accept_threshold,
    )

    settings = get_settings()
    monkeypatch.setattr(settings, "reference_matching_min_auto_accept_threshold", 90.0, raising=False)
    # A sneaky sub-floor value written directly to the row is clamped on read.
    row = _ensure_row(db)
    row.fuzzy_accept_threshold = 10.0
    db.flush()
    assert effective_fuzzy_accept_threshold(db) == 90.0
    # The update helper refuses sub-floor / >100 values outright.
    import pytest

    with pytest.raises(ValueError, match="between 90"):
        update_fuzzy_accept_threshold(db, value=50.0)
    with pytest.raises(ValueError):
        update_fuzzy_accept_threshold(db, value=101.0)
    assert update_fuzzy_accept_threshold(db, value=95.0) == 95.0
    assert effective_fuzzy_accept_threshold(db) == 95.0


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


def test_rejected_still_surfaces_a_different_better_candidate(db, monkeypatch) -> None:
    monkeypatch.setattr(  # soft-path test: keep the better candidate below auto-accept
        get_settings(), "reference_matching_high_confidence_threshold", 101.0, raising=False
    )
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


def test_per_paper_rescan_endpoint_links_reference(
    client, auth_headers, db, make_reference, monkeypatch
) -> None:
    monkeypatch.setattr(  # soft-path test: keep the match below the auto-accept threshold
        get_settings(), "reference_matching_high_confidence_threshold", 101.0, raising=False
    )
    citing = _work(db, "Citing Paper", year=2021)
    target = _work(db, LOCAL_TITLE, year=2010)
    make_reference(db, citing_work_id=citing.id, title=CITED_TITLE, year=2010)
    db.commit()

    r = client.post(f"/api/v1/works/{citing.id}/references/rescan", headers=auth_headers("editor"))
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


def test_reference_list_exposes_authors(client, auth_headers, db, make_reference) -> None:
    citing = _work(db, "Citing Paper", year=2021)
    make_reference(
        db,
        citing_work_id=citing.id,
        title="A Cited Paper",
        year=2019,
        authors=["Tenorth, M.", "Beetz, M."],
    )
    db.commit()
    refs = client.get(
        f"/api/v1/works/{citing.id}/references", headers=auth_headers("reader")
    ).json()
    assert refs[0]["authors"] == ["Tenorth, M.", "Beetz, M."]


def test_per_paper_rescan_requires_contributor(client, auth_headers, db, make_reference) -> None:
    citing = _work(db, "Citing Paper", year=2021)
    db.commit()
    r = client.post(f"/api/v1/works/{citing.id}/references/rescan", headers=auth_headers("reader"))
    assert r.status_code == 403


# --- confirm / reject / import actions (item #4) --------------------------------------------------


def _seed_likely(db, make_reference):
    citing = _work(db, "Citing Paper", year=2021)
    target = _work(db, LOCAL_TITLE, year=2010)
    ref = make_reference(
        db,
        citing_work_id=citing.id,
        title=CITED_TITLE,
        year=2010,
        suggested_work_id=target.id,
        match_score=98.0,
        resolution_status="likely_match",
    )
    db.commit()
    return citing, target, ref


def test_link_action_confirms_and_locks(client, auth_headers, db, make_reference) -> None:
    citing, target, ref = _seed_likely(db, make_reference)
    r = client.patch(
        f"/api/v1/works/{citing.id}/references/{ref.id}",
        headers=auth_headers("editor"),
        json={"action": "link"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["resolution_status"] == "confirmed_match"
    assert body["resolved_work_id"] == str(target.id)
    # A rescan must NOT revert a confirmed match.
    client.post(f"/api/v1/works/{citing.id}/references/rescan", headers=auth_headers("editor"))
    refs = client.get(
        f"/api/v1/works/{citing.id}/references", headers=auth_headers("editor")
    ).json()
    assert refs[0]["resolution_status"] == "confirmed_match"


def test_reject_action_keeps_suggestion(client, auth_headers, db, make_reference) -> None:
    citing, target, ref = _seed_likely(db, make_reference)
    r = client.patch(
        f"/api/v1/works/{citing.id}/references/{ref.id}",
        headers=auth_headers("editor"),
        json={"action": "reject"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["resolution_status"] == "rejected_match"
    assert body["suggested_work_id"] == str(target.id)  # kept for display
    assert body["resolved_work_id"] is None


def test_link_without_suggestion_is_conflict(client, auth_headers, db, make_reference) -> None:
    citing = _work(db, "Citing Paper", year=2021)
    ref = make_reference(
        db, citing_work_id=citing.id, title="Orphan ref", resolution_status="external"
    )
    db.commit()
    r = client.patch(
        f"/api/v1/works/{citing.id}/references/{ref.id}",
        headers=auth_headers("editor"),
        json={"action": "link"},
    )
    assert r.status_code == 409


def test_import_action_creates_work(client, auth_headers, db, make_reference) -> None:
    citing = _work(db, "Citing Paper", year=2021)
    ref = make_reference(
        db,
        citing_work_id=citing.id,
        title="A Missing Paper",
        year=2018,
        resolution_status="external",
    )
    db.commit()
    r = client.patch(
        f"/api/v1/works/{citing.id}/references/{ref.id}",
        headers=auth_headers("editor"),
        json={"action": "import"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["resolution_status"] == "local_match"
    assert body["resolved_work_id"] is not None


def test_action_on_reference_not_cited_by_work_is_404(
    client, auth_headers, db, make_reference
) -> None:
    citing = _work(db, "Citing Paper", year=2021)
    other = _work(db, "Other Paper", year=2021)
    ref = make_reference(
        db, citing_work_id=other.id, title="Elsewhere", resolution_status="external"
    )
    db.commit()
    r = client.patch(
        f"/api/v1/works/{citing.id}/references/{ref.id}",
        headers=auth_headers("editor"),
        json={"action": "reject"},
    )
    assert r.status_code == 404


def test_admin_toggle_round_trips_and_enqueues_rescan(client, auth_headers, monkeypatch) -> None:
    from app.workers import queue as queue_mod

    calls = []
    monkeypatch.setattr(queue_mod, "enqueue_reference_rescan", lambda: calls.append(1) or "job-1")
    admin = auth_headers("owner")
    assert (
        client.get("/api/v1/admin/app-config", headers=admin).json()["use_fuzzy_match_as_confirmed"]
        is False
    )
    r = client.patch(
        "/api/v1/admin/app-config", headers=admin, json={"use_fuzzy_match_as_confirmed": True}
    )
    assert r.status_code == 200
    assert r.json()["use_fuzzy_match_as_confirmed"] is True
    assert calls == [1]  # flipping ON kicks off a library-wide rescan


def test_admin_acceptance_settings_round_trip_and_validate(client, auth_headers, monkeypatch) -> None:
    """UX batch: the fuzzy threshold + high-confidence toggle are admin-editable; the yaml floor
    and the high-confidence threshold are surfaced read-only and enforced."""
    from app.workers import queue as queue_mod

    monkeypatch.setattr(queue_mod, "enqueue_reference_rescan", lambda: "job-1")
    admin = auth_headers("owner")
    cfg = client.get("/api/v1/admin/app-config", headers=admin).json()
    assert cfg["fuzzy_accept_threshold"] == 90.0  # yaml default
    assert cfg["fuzzy_accept_threshold_min"] == 90.0
    assert cfg["use_high_confidence_auto_accept"] is True
    assert cfg["high_confidence_threshold"] == 100.0

    r = client.patch(
        "/api/v1/admin/app-config",
        headers=admin,
        json={"fuzzy_accept_threshold": 95.5, "use_high_confidence_auto_accept": False},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["fuzzy_accept_threshold"] == 95.5
    assert body["use_high_confidence_auto_accept"] is False

    # Below the yaml floor → 400 (never persisted).
    r = client.patch(
        "/api/v1/admin/app-config", headers=admin, json={"fuzzy_accept_threshold": 10.0}
    )
    assert r.status_code == 400
    assert (
        client.get("/api/v1/admin/app-config", headers=admin).json()["fuzzy_accept_threshold"]
        == 95.5
    )


# --- arXiv-DOI bridging ---------------------------------------------------------------------------


def test_arxiv_doi_reference_matches_work_with_bare_arxiv_id(db) -> None:
    work = _work(db, "A Paper", arxiv_id="2101.00001", arxiv_base_id="2101.00001", year=2021)
    ref = _ref(db, title="A Paper", doi="10.48550/arXiv.2101.00001")
    resolve_and_persist(db, ref)
    assert ref.resolution_status == "local_match"
    assert ref.resolved_work_id == work.id


def test_arxiv_id_reference_matches_work_with_arxiv_doi(db) -> None:
    work = _work(db, "A Paper", doi="10.48550/arxiv.2101.00001", year=2021)
    ref = _ref(db, title="A Paper", arxiv_id="arXiv:2101.00001v3")
    resolve_and_persist(db, ref)
    assert ref.resolution_status == "local_match"
    assert ref.resolved_work_id == work.id


def test_arxiv_doi_vs_journal_doi_does_not_disqualify_fuzzy(db) -> None:
    # Preprint (arXiv DOI) cited; the published journal version (different DOI) is in the library.
    work = _work(db, LOCAL_TITLE, doi="10.1007/s10514-010-9200-5", year=2010)
    ref = _ref(db, title=CITED_TITLE, doi="10.48550/arXiv.1001.0001", year=2010)
    match = find_reference_match(db, ref)
    assert match is not None and match.via == "fuzzy"
    assert match.work_id == work.id


# --- fuzzy recall/precision refinements ------------------------------------------------------------


def test_leading_stopword_variant_still_blocks_and_matches(db) -> None:
    work = _work(db, "The Mathematical Theory of Communication", year=1948)
    ref = _ref(db, title="Mathematical theory of communication", year=1948)
    match = find_reference_match(db, ref)
    assert match is not None and match.work_id == work.id


def test_stopword_on_reference_side_still_blocks_and_matches(db) -> None:
    work = _work(db, "Mathematical Theory of Communication", year=1948)
    ref = _ref(db, title="The mathematical theory of communication", year=1948)
    match = find_reference_match(db, ref)
    assert match is not None and match.work_id == work.id


def test_year_off_by_one_still_matches(db) -> None:
    # Preprint year vs published year — the classic ±1 drift.
    work = _work(db, LOCAL_TITLE, year=2010)
    ref = _ref(db, title=CITED_TITLE, year=2009)
    match = find_reference_match(db, ref)
    assert match is not None and match.work_id == work.id


def test_year_off_by_two_is_rejected(db) -> None:
    _work(db, LOCAL_TITLE, year=2010)
    ref = _ref(db, title=CITED_TITLE, year=2008)
    assert find_reference_match(db, ref) is None


def test_short_generic_title_does_not_containment_match(db) -> None:
    # "Deep learning" is a strict token subset of the work title; token_set alone would score 100.
    _work(db, "Deep learning for medical imaging a survey", year=None)
    ref = _ref(db, title="Deep learning", year=None)
    assert find_reference_match(db, ref) is None


def test_long_subtitle_truncation_still_matches(db) -> None:
    work = _work(
        db, "Generative adversarial networks for image synthesis: methods and results", year=2019
    )
    ref = _ref(db, title="Generative adversarial networks for image synthesis", year=2019)
    match = find_reference_match(db, ref)
    assert match is not None and match.work_id == work.id


def test_reverse_rescan_picks_up_reference_by_arxiv_id(db) -> None:
    ref = _ref(
        db,
        title="Completely Different Spelling Of The Title",
        arxiv_id="arXiv:2101.00001v2",
        resolution_status="external",
    )
    new_work = _work(db, "A Paper", arxiv_id="2101.00001", arxiv_base_id="2101.00001", year=2021)
    changed = rescan_references_for_new_work(db, new_work)
    assert changed == 1
    assert ref.resolved_work_id == new_work.id
