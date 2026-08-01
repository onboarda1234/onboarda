"""P-03 — EDD routing divergence.

The probe re-runs the governed policy on the recorded fact contract. These tests
therefore never hardcode a route: they assert that the probe's answer tracks
``edd_routing_policy.evaluate_edd_routing``, which is what makes the finding
defensible rather than a second opinion.
"""

from __future__ import annotations

from supervisor_foundation.probes import edd_divergence
from supervisor_probe_fixtures import (
    AS_OF,
    add_edd_case,
    add_memo,
    approve,
    bundle_for,
    claims,
    only,
    routing_facts,
    run_probe,
    seed_application,
)

PROBE = edd_divergence.probe

#: Facts the governed policy routes to EDD. Asserted against the policy itself
#: in ``test_fixture_facts_actually_route_to_edd`` so the suite cannot drift
#: into testing a route the policy no longer produces.
EDD_FACTS = routing_facts(
    final_risk_level="HIGH",
    declared_pep_present=True,
    jurisdiction_risk_tier="high",
    edd_trigger_flags=["declared_pep_present"],
)


def _facts_route(facts):
    from edd_routing_policy import evaluate_edd_routing

    payload = dict(facts)
    payload.setdefault("supervisor_mandatory_escalation", False)
    return evaluate_edd_routing(payload)["route"]


def test_fixture_facts_actually_route_to_edd():
    """Guards the whole suite: these fixtures mean what the tests assume."""
    assert _facts_route(EDD_FACTS) == "edd"
    assert _facts_route(routing_facts()) == "standard"


# ── Availability and contract completeness ───────────────────────────


def test_no_memo_means_the_route_cannot_be_re_evaluated(db):
    app_id = seed_application(db)
    finding = only(run_probe(bundle_for(db, app_id), PROBE))
    assert finding["status"] == "unavailable"
    assert finding["availability_status"] == "data_absent"
    assert finding["status"] != "clear"


def test_memo_without_a_fact_contract_is_unavailable(db):
    app_id = seed_application(db)
    add_memo(db, app_id, include_contract=False)
    finding = only(run_probe(bundle_for(db, app_id), PROBE))
    assert finding["status"] == "unavailable"


def test_incomplete_contract_stops_evaluation_rather_than_guessing(db):
    """A truncated contract yields exactly one finding and no route claim.

    Scoring a route from partial facts would give a confident answer to a
    different question, so the probe must not also emit a divergence finding.
    """
    partial = routing_facts()
    partial.pop("jurisdiction_risk_tier")
    partial.pop("sector_risk_tier")

    app_id = seed_application(db)
    add_memo(db, app_id, facts=partial, stored_route="standard")
    findings = run_probe(bundle_for(db, app_id), PROBE)

    finding = only(findings)
    assert finding["status"] == "hit"
    assert finding["availability_status"] == "snapshot_incomplete"
    assert "jurisdiction_risk_tier" in finding["claim"]
    assert "sector_risk_tier" in finding["claim"]
    assert "yields route" not in finding["claim"]


# ── Check 1: policy requires EDD, no case exists ─────────────────────


def test_policy_requires_edd_and_no_case_exists(db):
    app_id = seed_application(db, risk_level="HIGH", final_risk_level="HIGH")
    add_memo(db, app_id, facts=EDD_FACTS, stored_route="edd",
             triggers=["declared_pep_present"])
    finding = only(run_probe(bundle_for(db, app_id), PROBE))
    assert finding["status"] == "hit"
    assert finding["severity"] == "high"
    assert "requires enhanced due diligence" in finding["claim"]
    assert "declared_pep_present" in finding["claim"]


def test_under_routing_on_an_approved_case_is_critical(db):
    """Severity turns on reliance: an officer approved on the gap."""
    app_id = seed_application(db, risk_level="HIGH", final_risk_level="HIGH")
    add_memo(db, app_id, facts=EDD_FACTS, stored_route="edd")
    approve(db, app_id, risk_level="HIGH")
    finding = only(run_probe(bundle_for(db, app_id), PROBE))
    assert finding["severity"] == "critical"
    assert "approved without one" in finding["claim"]


def test_existing_case_clears_the_under_routing_check(db):
    app_id = seed_application(db, risk_level="HIGH", final_risk_level="HIGH")
    add_memo(db, app_id, facts=EDD_FACTS, stored_route="edd")
    add_edd_case(db, app_id)
    finding = only(run_probe(bundle_for(db, app_id), PROBE))
    assert finding["status"] == "clear"


# ── Check 2: stored route says EDD but nothing happened ──────────────


def test_stored_edd_route_never_actuated(db):
    """Stored route 'edd' with standard-routing facts and no case.

    The memo recorded an escalation the policy does not derive and the queue
    never received. Both the divergence and the missing case are reported —
    they are different defects with different owners.
    """
    app_id = seed_application(db)
    add_memo(db, app_id, facts=routing_facts(), stored_route="edd")
    findings = run_probe(bundle_for(db, app_id), PROBE)

    assert len(findings) == 2
    assert len({finding["finding_id"] for finding in findings}) == 2
    text = claims(findings)
    assert "does not follow from the facts recorded" in text
    assert "recorded and never actuated" in text


# ── Check 3: stored route disagrees with the policy ──────────────────


def test_stored_standard_route_contradicts_the_policy(db):
    app_id = seed_application(db, risk_level="HIGH", final_risk_level="HIGH")
    add_memo(db, app_id, facts=EDD_FACTS, stored_route="standard")
    add_edd_case(db, app_id)
    finding = only(run_probe(bundle_for(db, app_id), PROBE))
    assert finding["category"] == "policy"
    assert "yields route 'edd'" in finding["claim"]
    assert "is 'standard'" in finding["claim"]


def test_policy_version_is_cited_as_the_basis(db):
    app_id = seed_application(db, risk_level="HIGH", final_risk_level="HIGH")
    add_memo(db, app_id, facts=EDD_FACTS, stored_route="standard",
             policy_version="edd_routing_policy_v9")
    add_edd_case(db, app_id)
    finding = only(run_probe(bundle_for(db, app_id), PROBE))
    assert "edd_routing_policy_v9" in finding["regulatory_or_policy_basis"]


# ── Check 5: conservative over-routing ───────────────────────────────


def test_over_routing_is_informational_not_a_control_failure(db):
    app_id = seed_application(db)
    add_memo(db, app_id, facts=routing_facts(), stored_route="standard")
    add_edd_case(db, app_id, stage="edd_approved")
    finding = only(run_probe(bundle_for(db, app_id), PROBE))
    assert finding["severity"] == "info"
    assert "more conservatively than policy required" in finding["claim"]
    assert finding["required_action"].startswith("None required")


def test_over_routing_alongside_another_defect_is_low_not_info(db):
    app_id = seed_application(db)
    add_memo(db, app_id, facts=routing_facts(), stored_route="edd")
    add_edd_case(db, app_id)
    findings = run_probe(bundle_for(db, app_id), PROBE)
    severities = {finding["category"]: finding["severity"] for finding in findings}
    assert severities["policy"] == "high"
    assert severities["edd"] == "low"


def test_over_routing_never_outranks_under_routing(db):
    """Extra diligence must never be scored above missing diligence."""
    order = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}

    over_id = seed_application(db)
    add_memo(db, over_id, facts=routing_facts(), stored_route="standard")
    add_edd_case(db, over_id)
    over = only(run_probe(bundle_for(db, over_id), PROBE))

    under_id = seed_application(db, risk_level="HIGH", final_risk_level="HIGH")
    add_memo(db, under_id, facts=EDD_FACTS, stored_route="edd")
    under = only(run_probe(bundle_for(db, under_id), PROBE))

    assert order[over["severity"]] < order[under["severity"]]


# ── Determinism and clock discipline ─────────────────────────────────


def test_evaluated_at_is_excluded_from_the_comparison(db):
    """The policy stamps a wall clock; two runs must still agree."""
    from edd_routing_policy import evaluate_edd_routing

    payload = dict(EDD_FACTS)
    payload["supervisor_mandatory_escalation"] = False
    assert "evaluated_at" in evaluate_edd_routing(payload)

    app_id = seed_application(db, risk_level="HIGH", final_risk_level="HIGH")
    add_memo(db, app_id, facts=EDD_FACTS, stored_route="edd")
    bundle = bundle_for(db, app_id)
    assert run_probe(bundle, PROBE) == run_probe(bundle, PROBE)


def test_as_of_does_not_affect_the_outcome(db):
    app_id = seed_application(db, risk_level="HIGH", final_risk_level="HIGH")
    add_memo(db, app_id, facts=EDD_FACTS, stored_route="edd")
    early = run_probe(bundle_for(db, app_id, as_of="2020-01-01T00:00:00Z"), PROBE)
    late = run_probe(bundle_for(db, app_id, as_of=AS_OF), PROBE)
    assert [f["claim"] for f in early] == [f["claim"] for f in late]


# ── Scope discipline ─────────────────────────────────────────────────


def test_probe_creates_no_edd_case(db):
    app_id = seed_application(db, risk_level="HIGH", final_risk_level="HIGH")
    add_memo(db, app_id, facts=EDD_FACTS, stored_route="edd")
    before = db.execute("SELECT COUNT(*) AS n FROM edd_cases").fetchone()["n"]
    run_probe(bundle_for(db, app_id), PROBE)
    after = db.execute("SELECT COUNT(*) AS n FROM edd_cases").fetchone()["n"]
    assert before == after


def test_memo_time_claims_are_not_worded_as_current_state(db):
    """The fact contract is a snapshot. A claim about it says so."""
    app_id = seed_application(db, risk_level="HIGH", final_risk_level="HIGH")
    add_memo(db, app_id, facts=EDD_FACTS, stored_route="edd")
    finding = only(run_probe(bundle_for(db, app_id), PROBE))
    assert "recorded for this application" in finding["claim"]
    lowered = finding["claim"].lower()
    assert "the customer is currently" not in lowered
    assert "the customer requires" not in lowered


def test_incomplete_contract_case_does_not_restate_the_approval_blocker(db):
    """An open EDD case on an approved application is the gate's job.

    ``security_hardening`` blocks approval unless an approved EDD case covers
    the current triggers, so P-03 must not emit a finding for it.
    """
    app_id = seed_application(db, risk_level="HIGH", final_risk_level="HIGH")
    add_memo(db, app_id, facts=EDD_FACTS, stored_route="edd")
    add_edd_case(db, app_id, stage="triggered")
    approve(db, app_id, risk_level="HIGH")
    findings = run_probe(bundle_for(db, app_id), PROBE)
    assert only(findings)["status"] == "clear"
