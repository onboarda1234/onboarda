"""P-06 — monitoring requirement not established.

The sharpest thing this probe must get right is its own date handling. The
platform's ``periodic_review_policy.parse_review_date`` returns *today* for
unreadable input, which would turn the most defective record in the database
into a passing check. Several tests below exist solely to prove that fallback
never reaches the Supervisor.
"""

from __future__ import annotations

from datetime import date

import pytest

from supervisor_foundation.probes import monitoring_requirement
from supervisor_probe_fixtures import (
    AS_OF,
    add_periodic_review,
    approve,
    bundle_for,
    claims,
    only,
    run_probe,
    seed_application,
)

PROBE = monitoring_requirement.probe


def _approved(db, **overrides):
    app_id = seed_application(db, **overrides)
    # ``decision_records.risk_level`` is CHECK-constrained to the governed
    # levels, so the decision mirrors ``risk_level`` rather than the (freely
    # writable) ``final_risk_level`` the probe reads.
    approve(db, app_id, risk_level=overrides.get("risk_level", "MEDIUM"))
    return app_id


# ── Check 5: scope ───────────────────────────────────────────────────


def test_unapproved_case_carries_no_obligation(db):
    app_id = seed_application(db)
    finding = only(run_probe(bundle_for(db, app_id), PROBE))
    assert finding["status"] == "not_applicable"
    assert finding["severity"] == "info"


def test_not_applicable_is_distinct_from_clear(db):
    """A case out of scope must not be counted as a check that passed."""
    app_id = seed_application(db)
    assert only(run_probe(bundle_for(db, app_id), PROBE))["status"] != "clear"


# ── Check 1: approved with no schedule at all ────────────────────────


def test_approved_customer_with_no_periodic_review(db):
    app_id = _approved(db)
    finding = only(run_probe(bundle_for(db, app_id), PROBE))
    assert finding["status"] == "hit"
    assert "no periodic review record exists" in finding["claim"]


@pytest.mark.parametrize(
    "risk_level,expected_severity",
    [("LOW", "medium"), ("MEDIUM", "medium"), ("HIGH", "high"), ("VERY_HIGH", "high")],
)
def test_missing_schedule_severity_tracks_the_governed_cycle(
    db, risk_level, expected_severity
):
    app_id = _approved(db, risk_level=risk_level, final_risk_level=risk_level)
    finding = only(run_probe(bundle_for(db, app_id), PROBE))
    assert finding["severity"] == expected_severity


def test_claim_quotes_the_governed_frequency_for_the_level(db):
    from periodic_review_policy import RISK_FREQUENCY_MONTHS

    app_id = _approved(db, risk_level="HIGH", final_risk_level="HIGH")
    finding = only(run_probe(bundle_for(db, app_id), PROBE))
    assert f"every {RISK_FREQUENCY_MONTHS['HIGH']} months" in finding["claim"]
    assert str(RISK_FREQUENCY_MONTHS["HIGH"]) in finding["regulatory_or_policy_basis"]


# ── Risk level integrity ─────────────────────────────────────────────


def test_absent_risk_level_is_unavailable(db):
    app_id = _approved(db, final_risk_level=None)
    finding = only(run_probe(bundle_for(db, app_id), PROBE))
    assert finding["status"] == "unavailable"


def test_ungoverned_risk_level_is_refused_not_silently_normalised(db):
    """``normalize_risk_level`` maps anything unknown to MEDIUM.

    Inheriting that would attribute a governed 24-month cycle to a level the
    policy does not govern — a false attribution dressed as compliance.

    Reachable in practice: ``applications.risk_level`` carries a CHECK
    constraint on the four governed levels, but ``final_risk_level`` — the
    column this probe reads — was added later and has none.
    """
    from periodic_review_policy import frequency_months_for_risk

    assert frequency_months_for_risk("CRITICAL") == 24, (
        "upstream fallback changed — re-check whether the probe's guard is "
        "still needed"
    )

    app_id = _approved(db, risk_level="HIGH", final_risk_level="CRITICAL")
    finding = only(run_probe(bundle_for(db, app_id), PROBE))
    assert finding["status"] == "unavailable"
    assert "not one of the governed levels" in finding["claim"]
    assert "24" not in finding["claim"]


# ── Check 4: date handling, the clock hazard ─────────────────────────


def test_parse_iso_date_returns_none_rather_than_today():
    """The whole reason this probe carries its own parser."""
    for bad in ("", None, "not-a-date", "2026-13-45", "soon"):
        assert monitoring_requirement.parse_iso_date(bad) is None
    assert monitoring_requirement.parse_iso_date("2027-01-20") == date(2027, 1, 20)
    assert monitoring_requirement.parse_iso_date("2027-01-20T09:00:00Z") == date(
        2027, 1, 20
    )


def test_prohibited_helper_would_have_returned_today():
    """Demonstrates the hazard the probe is avoiding, on the real function."""
    from datetime import date as real_date

    from periodic_review_policy import parse_review_date

    assert parse_review_date("not-a-date") == real_date.today()
    assert monitoring_requirement.parse_iso_date("not-a-date") is None


def test_probe_does_not_call_the_prohibited_helper():
    """Asserted over the syntax tree, not the text.

    The prohibited name appears in the module's prose, explaining why it is
    prohibited. A substring check would either fail on that documentation or be
    weakened until it proved nothing. The AST answers the real question: does
    any identifier in executable code refer to it?
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(monitoring_requirement))
    referenced = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    } | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    } | {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert "parse_review_date" not in referenced


def test_unreadable_next_review_date_is_a_hit_not_a_pass(db):
    app_id = _approved(db)
    add_periodic_review(db, app_id, next_review_date="whenever")
    findings = run_probe(bundle_for(db, app_id), PROBE)
    finding = next(f for f in findings if "not a readable date" in f["claim"])
    assert finding["status"] == "hit"
    assert finding["severity"] == "high"


def test_null_next_review_date_is_a_hit_not_a_pass(db):
    app_id = _approved(db)
    add_periodic_review(db, app_id, next_review_date=None)
    assert "not a readable date" in claims(run_probe(bundle_for(db, app_id), PROBE))


# ── Check 2: scheduled beyond the permitted frequency ────────────────


def test_cycle_longer_than_policy_permits_is_a_hit(db):
    """MEDIUM permits 24 months from the last review; this is 30."""
    app_id = _approved(db)
    add_periodic_review(
        db, app_id, last_review_date="2026-01-15", next_review_date="2028-07-15"
    )
    finding = next(
        f for f in run_probe(bundle_for(db, app_id), PROBE)
        if "later than the" in f["claim"]
    )
    assert finding["status"] == "hit"
    assert "2028-01-15 maximum" in finding["claim"]


def test_cycle_exactly_at_the_permitted_boundary_is_not_a_hit(db):
    app_id = _approved(db)
    add_periodic_review(
        db, app_id, last_review_date="2026-01-15", next_review_date="2028-01-15"
    )
    assert "later than the" not in claims(run_probe(bundle_for(db, app_id), PROBE))


def test_anchor_precedence_last_review_then_decision_then_as_of(db):
    """Three anchors, fixed precedence, so the answer is deterministic."""
    # No last_review_date: the approval date anchors the cycle.
    from_decision = _approved(db)
    add_periodic_review(
        db, from_decision, last_review_date=None, next_review_date="2028-08-20"
    )
    finding = next(
        f for f in run_probe(bundle_for(db, from_decision), PROBE)
        if "later than the" in f["claim"]
    )
    assert "from 2026-07-20" in finding["claim"]

    # A last_review_date takes precedence over the approval date.
    from_review = _approved(db)
    add_periodic_review(
        db, from_review, last_review_date="2026-02-01", next_review_date="2028-08-20"
    )
    finding = next(
        f for f in run_probe(bundle_for(db, from_review), PROBE)
        if "later than the" in f["claim"]
    )
    assert "from 2026-02-01" in finding["claim"]


def test_add_months_clamps_to_a_short_month(db):
    assert monitoring_requirement.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert monitoring_requirement.add_months(date(2026, 8, 31), 6) == date(2027, 2, 28)
    assert monitoring_requirement.add_months(date(2026, 3, 15), 12) == date(2027, 3, 15)


# ── Governing-review selection ───────────────────────────────────────


def test_terminal_states_match_the_authoritative_set():
    """The probe's closed-state set is the engine's, not a private opinion."""
    from periodic_review_engine import TERMINAL_REVIEW_STATES as AUTHORITATIVE

    assert monitoring_requirement.TERMINAL_REVIEW_STATES == frozenset(AUTHORITATIVE)


def test_a_closed_review_never_governs_over_the_open_one(db):
    """The systematic false negative this probe would otherwise have.

    Completing a review inserts the next cycle as a new pending row, so from the
    second cycle onward every customer has closed rows whose next_review_date is
    in the past — always earlier than the live one. Governing on the earliest
    date across all rows would silently grade a closed historical cycle and
    report clear while the live schedule went unexamined.
    """
    app_id = _approved(db)
    add_periodic_review(
        db, app_id, last_review_date="2024-01-15", next_review_date="2026-01-15",
        status="completed",
    )
    add_periodic_review(
        db, app_id, last_review_date="2026-01-15", next_review_date="2029-06-01",
        status="pending",
    )
    finding = next(
        f for f in run_probe(bundle_for(db, app_id), PROBE)
        if "later than the" in f["claim"]
    )
    assert "2029-06-01" in finding["claim"], (
        "the probe graded a closed cycle instead of the live one"
    )


def test_only_closed_reviews_is_a_hit_not_a_clear(db):
    """A file holding only closed reviews has fallen out of the schedule."""
    app_id = _approved(db)
    add_periodic_review(db, app_id, next_review_date="2027-06-01", status="completed")
    add_periodic_review(db, app_id, next_review_date="2027-09-01", status="cancelled")
    finding = only(run_probe(bundle_for(db, app_id), PROBE))
    assert finding["status"] == "hit"
    assert "completed or cancelled" in finding["claim"]
    assert finding["status"] != "clear"


def test_a_cancelled_review_alone_does_not_satisfy_the_obligation(db):
    app_id = _approved(db)
    add_periodic_review(db, app_id, next_review_date="2027-06-01", status="cancelled")
    finding = only(run_probe(bundle_for(db, app_id), PROBE))
    assert finding["status"] == "hit"


def test_a_closed_review_alongside_a_compliant_open_one_clears(db):
    """History must not manufacture a finding either."""
    app_id = _approved(db)
    add_periodic_review(
        db, app_id, last_review_date="2024-01-15", next_review_date="2026-01-15",
        status="completed",
    )
    add_periodic_review(
        db, app_id, last_review_date="2026-01-15", next_review_date="2027-06-01",
        status="pending",
    )
    assert only(run_probe(bundle_for(db, app_id), PROBE))["status"] == "clear"


def test_earliest_readable_open_review_governs(db):
    """The nearest commitment is the one the institution is held to."""
    app_id = _approved(db)
    add_periodic_review(db, app_id, next_review_date="2029-01-01")
    add_periodic_review(db, app_id, next_review_date="2027-06-01")
    finding = only(run_probe(bundle_for(db, app_id), PROBE))
    assert finding["status"] == "clear"
    assert "2027-06-01" in finding["claim"]


def test_probe_does_not_restate_the_overdue_surface(db):
    """Overdue reviews are already escalated by monitoring_sla; not restated.

    A review whose date has passed relative to ``as_of`` is not a P-06 finding.
    The monitoring surface owns overdue; this probe owns whether a governed
    schedule exists at all and whether its cycle is within policy.
    """
    app_id = _approved(db)
    add_periodic_review(
        db, app_id, last_review_date="2026-01-15", next_review_date="2026-03-01"
    )
    assert only(run_probe(bundle_for(db, app_id), PROBE))["status"] == "clear"


# ── Check 3: policy version attribution ──────────────────────────────


def test_null_policy_version_is_a_hit(db):
    app_id = _approved(db)
    add_periodic_review(db, app_id, policy_version=None)
    finding = next(
        f for f in run_probe(bundle_for(db, app_id), PROBE)
        if "records no policy version" in f["claim"]
    )
    assert finding["status"] == "hit"


def test_recorded_policy_version_clears_the_check(db):
    app_id = _approved(db)
    add_periodic_review(db, app_id, policy_version="periodic_review_policy_v2")
    assert only(run_probe(bundle_for(db, app_id), PROBE))["status"] == "clear"


def test_date_and_version_defects_are_separate_findings(db):
    app_id = _approved(db)
    add_periodic_review(db, app_id, next_review_date="whenever", policy_version=None)
    findings = run_probe(bundle_for(db, app_id), PROBE)
    assert len(findings) == 2
    assert len({f["finding_id"] for f in findings}) == 2
    assert len({f["primary_evidence_ref"] for f in findings}) == 2


# ── Clear ────────────────────────────────────────────────────────────


def test_compliant_schedule_records_an_explicit_clear(db):
    app_id = _approved(db)
    add_periodic_review(db, app_id, next_review_date="2027-06-01")
    finding = only(run_probe(bundle_for(db, app_id), PROBE))
    assert finding["status"] == "clear"
    assert finding["severity"] == "info"


# ── Stated limitation ────────────────────────────────────────────────


def test_findings_state_the_memo_prose_limitation(db):
    """The probe must not imply it covers commitments made only in prose."""
    app_id = _approved(db)
    finding = only(run_probe(bundle_for(db, app_id), PROBE))
    assert monitoring_requirement.PROSE_LIMITATION in finding["why_it_matters"]


# ── Determinism ──────────────────────────────────────────────────────


def test_findings_are_stable_across_repeated_runs(db):
    app_id = _approved(db)
    add_periodic_review(db, app_id, next_review_date="whenever", policy_version=None)
    bundle = bundle_for(db, app_id)
    assert run_probe(bundle, PROBE) == run_probe(bundle, PROBE)


def test_as_of_only_matters_as_a_fallback_anchor(db):
    """With a last_review_date present, the review instant changes nothing."""
    app_id = _approved(db)
    add_periodic_review(
        db, app_id, last_review_date="2026-01-15", next_review_date="2028-07-15"
    )
    early = run_probe(bundle_for(db, app_id, as_of="2026-02-01T00:00:00Z"), PROBE)
    late = run_probe(bundle_for(db, app_id, as_of=AS_OF), PROBE)
    assert [f["claim"] for f in early] == [f["claim"] for f in late]


def test_probe_never_states_a_customer_is_compliant(db):
    app_id = _approved(db)
    add_periodic_review(db, app_id, next_review_date="2027-06-01")
    text = claims(run_probe(bundle_for(db, app_id), PROBE)).lower()
    for forbidden in ("is compliant", "non-compliant", "is not compliant"):
        assert forbidden not in text
