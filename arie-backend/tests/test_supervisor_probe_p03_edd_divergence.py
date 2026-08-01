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

    Two findings with different owners and different confidence. The missing
    case rests on the *stored* route and is asserted outright. The route
    comparison rests on a re-run whose inputs are incomplete, so it is reported
    as not replayable rather than as divergence.
    """
    app_id = seed_application(db)
    add_memo(db, app_id, facts=routing_facts(), stored_route="edd")
    findings = run_probe(bundle_for(db, app_id), PROBE)

    assert len(findings) == 2
    assert len({finding["finding_id"] for finding in findings}) == 2
    text = claims(findings)
    assert "recorded and never actuated" in text

    replay = next(f for f in findings if f["status"] == "not_replayable")
    assert replay["availability_status"] == "snapshot_incomplete"
    assert "sector_label" in replay["claim"]
    assert "does not follow from the facts recorded" not in replay["claim"]


def test_stored_edd_with_standard_recompute_is_never_reported_as_divergence(db):
    """The false-positive class this probe must not have.

    An absent policy input can only remove triggers, so "policy says standard,
    file says EDD" is exactly what an incomplete contract looks like. Asserting
    divergence there would accuse an officer of escalating beyond policy on
    evidence that cannot support the claim.
    """
    app_id = seed_application(db)
    add_memo(db, app_id, facts=routing_facts(), stored_route="edd")
    add_edd_case(db, app_id)
    finding = only(run_probe(bundle_for(db, app_id), PROBE))
    assert finding["status"] == "not_replayable"
    assert finding["status"] != "clear"
    assert finding["severity"] == "low"


def test_crypto_sector_label_is_the_known_replay_gap():
    """Names the gap the safe-direction rule exists for, on the real policy.

    ``sector_label`` is read by ``evaluate_edd_routing`` but is not in
    ``REQUIRED_FACT_KEYS`` and so is not carried in the bundle. If it is ever
    carried, this fails and the safe-direction restriction can be lifted.
    """
    from edd_routing_policy import REQUIRED_FACT_KEYS, evaluate_edd_routing

    from supervisor_foundation.bundle import EDD_ROUTING_FACT_KEYS

    for key in edd_divergence.UNREPLAYABLE_POLICY_KEYS:
        assert key not in REQUIRED_FACT_KEYS
        assert key not in EDD_ROUTING_FACT_KEYS

    base = dict(routing_facts(), supervisor_mandatory_escalation=False)
    with_label = dict(base, sector_label="Crypto / Digital Assets Exchange")
    assert evaluate_edd_routing(with_label)["route"] == "edd"
    assert evaluate_edd_routing(base)["route"] == "standard"


# ── Check 3: stored route disagrees with the policy ──────────────────


def test_stored_standard_route_contradicts_the_policy(db):
    """The safe direction: no absent key could have manufactured this."""
    app_id = seed_application(db, risk_level="HIGH", final_risk_level="HIGH")
    add_memo(db, app_id, facts=EDD_FACTS, stored_route="standard")
    add_edd_case(db, app_id)
    finding = only(run_probe(bundle_for(db, app_id), PROBE))
    assert finding["category"] == "policy"
    assert finding["status"] == "hit"
    assert finding["severity"] == "high"
    assert "requires enhanced due diligence" in finding["claim"]
    assert "is 'standard'" in finding["claim"]


def test_policy_version_is_cited_as_the_basis(db):
    app_id = seed_application(db, risk_level="HIGH", final_risk_level="HIGH")
    add_memo(db, app_id, facts=EDD_FACTS, stored_route="standard",
             policy_version="edd_routing_policy_v9")
    add_edd_case(db, app_id)
    finding = only(run_probe(bundle_for(db, app_id), PROBE))
    assert "edd_routing_policy_v9" in finding["regulatory_or_policy_basis"]


# ── Check 5: conservative over-routing ───────────────────────────────


def test_conservative_over_routing_produces_no_finding(db):
    """An EDD case beyond what policy requires is not reported at all.

    The check that would report it rests on "the policy says standard", which
    an absent input can manufacture. Extra diligence is not a control failure,
    so an unfalsifiable claim about it is pure noise and the check is absent.
    """
    app_id = seed_application(db)
    add_memo(db, app_id, facts=routing_facts(), stored_route="standard")
    add_edd_case(db, app_id, stage="edd_approved")
    finding = only(run_probe(bundle_for(db, app_id), PROBE))
    assert finding["status"] == "clear"
    assert "conservativ" not in claims([finding]).lower()


def test_under_routing_is_the_highest_severity_the_probe_emits(db):
    """Missing diligence outranks everything else P-03 can say."""
    order = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}

    under_id = seed_application(db, risk_level="HIGH", final_risk_level="HIGH")
    add_memo(db, under_id, facts=EDD_FACTS, stored_route="edd")
    approve(db, under_id, risk_level="HIGH")
    under = only(run_probe(bundle_for(db, under_id), PROBE))
    assert under["severity"] == "critical"

    replay_id = seed_application(db)
    add_memo(db, replay_id, facts=routing_facts(), stored_route="edd")
    add_edd_case(db, replay_id)
    replay = only(run_probe(bundle_for(db, replay_id), PROBE))
    assert order[replay["severity"]] < order[under["severity"]]


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


# ── Replay fidelity ──────────────────────────────────────────────────
# The defect class that motivated the safe-direction rule: the bundle projects
# ``REQUIRED_FACT_KEYS`` — the policy's *completeness* contract — and an earlier
# draft treated that as its *read* set. The policy reads more. These two tests
# make that gap impossible to widen silently.


def test_probe_supplies_every_policy_input_except_the_declared_gap():
    """Static: AST-scan the policy for the keys it reads.

    A new ``facts.get("…")`` added to ``edd_routing_policy`` fails here rather
    than silently degrading every recomputed route in production.
    """
    import ast
    import inspect

    import edd_routing_policy

    from supervisor_foundation.bundle import EDD_ROUTING_FACT_KEYS

    tree = ast.parse(inspect.getsource(edd_routing_policy.evaluate_edd_routing))
    read = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "facts"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }
    assert read, "the policy read-set scan found nothing — the scan has drifted"

    supplied = set(EDD_ROUTING_FACT_KEYS) | {
        "supervisor_mandatory_escalation",
        "supervisor_mandatory_escalation_reasons",
    }
    unsupplied = read - supplied
    assert unsupplied == set(edd_divergence.UNREPLAYABLE_POLICY_KEYS), (
        f"the policy reads {sorted(unsupplied)}; the probe declares its gap as "
        f"{sorted(edd_divergence.UNREPLAYABLE_POLICY_KEYS)}. Either supply the "
        "key or declare it, but do not let a recomputed route rest on an "
        "input nobody has accounted for."
    )


def test_probe_reproduces_the_route_a_real_memo_was_routed_on(db):
    """Behavioural: capture what ``memo_handler`` fed the policy and compare.

    The strongest available form of the claim "this probe measures the decision
    path's own inputs". Missing ``supervisor_mandatory_escalation_reasons``
    alone caused 14 of 15 trigger-set mismatches against the canonical corpus.
    """
    import memo_handler
    from edd_routing_policy import evaluate_edd_routing

    captured = {}
    real = memo_handler._evaluate_edd_routing

    def spy(facts):
        captured["facts"] = dict(facts)
        return real(facts)

    app_id = seed_application(
        db, risk_level="HIGH", final_risk_level="HIGH",
        sector="Software / SaaS", country="Nigeria",
    )
    row = dict(db.execute(
        "SELECT * FROM applications WHERE id = ?", (app_id,)
    ).fetchone())

    memo_handler._evaluate_edd_routing = spy
    try:
        memo, *_ = memo_handler.build_compliance_memo(row, [], [], [])
    finally:
        memo_handler._evaluate_edd_routing = real

    assert "facts" in captured, "memo generation did not reach the routing policy"

    import json

    db.execute(
        "INSERT INTO compliance_memos (application_id, version, memo_data, "
        "validation_status, quality_score) VALUES (?,?,?,?,?)",
        (app_id, 1, json.dumps(memo, default=str), "pass", 0.9),
    )
    db.commit()

    bundle = bundle_for(db, app_id)
    stored = (bundle.get("edd") or {}).get("routing_facts") or {}
    assert stored.get("present") and not stored.get("absent_fact_keys")

    verdict = (bundle.get("supervisor_verdict") or {}).get("verdict") or {}
    reconstructed = dict(stored.get("facts") or {})
    reconstructed["supervisor_mandatory_escalation"] = bool(
        verdict.get("mandatory_escalation")
    )
    reconstructed["supervisor_mandatory_escalation_reasons"] = list(
        verdict.get("mandatory_escalation_reasons") or []
    )

    at_memo_time = evaluate_edd_routing(captured["facts"])
    at_review_time = evaluate_edd_routing(reconstructed)

    assert at_review_time["route"] == at_memo_time["route"]
    # Triggers may be a strict subset only for the declared gap; nothing the
    # probe reconstructs may ever add a trigger memo time did not have.
    assert set(at_review_time["triggers"]) <= set(at_memo_time["triggers"])
    assert set(at_memo_time["triggers"]) - set(at_review_time["triggers"]) == set()
