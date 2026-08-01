"""P-03 — EDD routing divergence.

**What it asks.** Re-running the governed routing policy on the *same fact
contract the decision path used*, does the answer still match what was stored,
and did the operational world act on it?

**Three distinct states, never conflated.** The probe reasons across a chain
that no screen shows together:

1. **memo-time facts** — ``edd.routing_facts``, the contract
   ``memo_handler`` recorded and fed to the policy. A point-in-time snapshot.
2. **stored routing outcome** — ``edd.stored_routing``, the route the memo
   recorded at that same moment.
3. **current EDD case state** — ``edd.cases``, the operational world now.

A claim about (1) is never worded as a statement about current state.

**Why it is not a restatement.** The EDD queue shows whether a case exists. It
does not show that the governed policy, run on the recorded facts, required one.
``evaluate_edd_routing`` is pure and versioned, so it can be re-run at any time —
and nothing in the product does.

**What it must never do.** Create, update or complete an EDD case.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..contracts import AvailabilityStatus, FindingStatus
from ._draft import (
    CRITICAL,
    HIGH,
    INFO,
    LOW,
    MEDIUM,
    approval_refs,
    finding_draft,
    is_approved,
    severity_by_reliance,
    subject_ref,
    unique_refs,
)

PROBE_ID = "P-03"
PROBE_VERSION = "p03-v1"

SOURCE_MODULES = (
    "edd_routing_policy.evaluate_edd_routing",
    "memo_handler.agent5_input_contract",
    "supervisor_foundation.bundle",
)

#: Emitted by the policy and excluded from every comparison — a wall-clock
#: field that would differ between two runs of an identical evaluation.
COSMETIC_KEYS = ("evaluated_at",)

ROUTE_EDD = "edd"
ROUTE_STANDARD = "standard"

#: **Deliberately not checked here: an EDD case opened and never completed.**
#: The approval gate already blocks final approval unless
#: ``edd_completion.collect_edd_completion_status`` reports an approved EDD case
#: covering the current triggers (``security_hardening._approval_edd_completion_*``).
#: Restating an enforced blocker as a Supervisor finding would add noise and no
#: information — the no-restatement rule. What the gate cannot see, and this
#: probe can, is whether the *route itself* follows from the recorded facts.


def _comparable(routing: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in routing.items() if key not in COSMETIC_KEYS}


def _base_refs(bundle: Mapping[str, Any], routing_facts: Mapping[str, Any]) -> list[str]:
    subject = subject_ref(bundle)
    facts = routing_facts.get("facts") or {}
    refs = [
        f"{subject}#edd_routing_facts",
        f"edd_policy:{(bundle.get('edd') or {}).get('stored_routing', {}).get('policy_version')}",
    ]
    for trigger in facts.get("edd_trigger_flags") or []:
        refs.append(f"edd_policy:trigger:{trigger}")
    for case in (bundle.get("edd") or {}).get("cases") or []:
        if isinstance(case, Mapping):
            refs.append(f"edd_case:{case.get('id')}")
    refs.extend(approval_refs(bundle))
    return unique_refs(refs)


def probe(bundle: Mapping[str, Any], policy: Any) -> Sequence[Mapping[str, Any]]:
    """Run P-03 over a bundle. Pure; no clock, no database, no provider."""
    edd = bundle.get("edd") or {}
    routing_facts = edd.get("routing_facts") or {}
    subject = subject_ref(bundle)

    # Identity anchors, one per aspect examined. Route disagreement and a
    # missing case can fire together, and both would otherwise share the same
    # lowest-sorting reference; naming the aspect keeps them separate findings.
    anchor_contract = f"{subject}#edd_routing_facts"
    anchor_route = f"{subject}#edd_route"
    anchor_case = f"{subject}#edd_case_existence"

    # ── Availability ──────────────────────────────────────────────
    if not routing_facts.get("present"):
        return [
            finding_draft(
                probe_id=PROBE_ID,
                probe_version=PROBE_VERSION,
                category="edd",
                severity=MEDIUM,
                status=FindingStatus.UNAVAILABLE,
                availability_status=AvailabilityStatus.DATA_ABSENT,
                confidence=1.0,
                claim=(
                    "EDD routing cannot be re-evaluated: no stored routing fact "
                    "contract exists for this application, so the inputs the "
                    "routing decision used were never recorded."
                ),
                evidence_refs=unique_refs([f"{subject}#edd_routing_facts"]),
                primary_evidence_ref=f"{subject}#edd_routing_facts",
                source_modules=list(SOURCE_MODULES),
                why_it_matters=(
                    "Without the recorded fact contract the routing decision "
                    "cannot be reproduced or challenged. This check did not run; "
                    "its result must not be read as agreement with the route."
                ),
                regulatory_or_policy_basis="internal_policy_only",
                officer_question=(
                    "Has a compliance memo been generated for this application?"
                ),
                required_action=(
                    "Generate the compliance memo through the existing "
                    "authoritative path so the routing fact contract is recorded."
                ),
                close_condition=(
                    "memo metadata carries agent5_input_contract and this check "
                    "can be re-run."
                ),
            )
        ]

    # ── Check 4 — incomplete contract, evaluated before anything else ──
    # A partial evaluation would be worse than none: the policy treats a missing
    # key as `incomplete_contract`, so scoring a route from a truncated contract
    # would produce a confident answer to the wrong question.
    absent = list(routing_facts.get("absent_fact_keys") or [])
    if absent:
        return [
            finding_draft(
                probe_id=PROBE_ID,
                probe_version=PROBE_VERSION,
                category="edd",
                severity=HIGH,
                status=FindingStatus.HIT,
                availability_status=AvailabilityStatus.SNAPSHOT_INCOMPLETE,
                confidence=1.0,
                claim=(
                    "The stored EDD routing fact contract is incomplete: "
                    f"{sorted(absent)} were not recorded. The governed policy "
                    "treats a missing fact as an incomplete contract, so the "
                    "route this case took cannot be reproduced from what was "
                    "stored."
                ),
                evidence_refs=unique_refs(
                    [*_base_refs(bundle, routing_facts), anchor_contract]
                ),
                primary_evidence_ref=anchor_contract,
                source_modules=list(SOURCE_MODULES),
                why_it_matters=(
                    "A routing decision that cannot be reproduced cannot be "
                    "defended. The probe deliberately stops here rather than "
                    "scoring a route from a truncated contract, which would give "
                    "a confident answer to a different question."
                ),
                regulatory_or_policy_basis="internal_policy_only",
                officer_question=(
                    "Why were these facts missing when the routing decision was "
                    "recorded?"
                ),
                required_action=(
                    "Regenerate the memo so a complete fact contract is recorded, "
                    "then re-run this check."
                ),
                close_condition=(
                    "agent5_input_contract records every REQUIRED_FACT_KEY and "
                    "the recomputed route can be compared to the stored route."
                ),
            )
        ]

    # ── Re-run the governed policy on the recorded facts ──────────
    from edd_routing_policy import evaluate_edd_routing  # local: pure, versioned

    facts = dict(routing_facts.get("facts") or {})
    facts["supervisor_mandatory_escalation"] = bool(
        ((bundle.get("supervisor_verdict") or {}).get("verdict") or {}).get(
            "mandatory_escalation"
        )
    )
    recomputed = _comparable(evaluate_edd_routing(facts))
    recomputed_route = str(recomputed.get("route") or "").strip().lower()

    stored_routing = edd.get("stored_routing") or {}
    stored_route = str(stored_routing.get("route") or "").strip().lower()

    cases = [case for case in (edd.get("cases") or []) if isinstance(case, Mapping)]
    has_case = bool(cases)
    approved = is_approved(bundle)
    refs = _base_refs(bundle, routing_facts)
    fired = sorted(str(trigger) for trigger in (recomputed.get("triggers") or []))

    drafts: list[dict[str, Any]] = []

    # ── Check 3 — the stored route disagrees with the policy ──────
    if stored_route and recomputed_route and stored_route != recomputed_route:
        drafts.append(
            finding_draft(
                probe_id=PROBE_ID,
                probe_version=PROBE_VERSION,
                category="policy",
                severity=HIGH,
                status=FindingStatus.HIT,
                availability_status=AvailabilityStatus.AVAILABLE,
                confidence=1.0,
                claim=(
                    f"Re-running the governed EDD policy on the recorded fact "
                    f"contract yields route '{recomputed_route}', but the stored "
                    f"routing outcome for this application is '{stored_route}'. "
                    "The stored outcome does not follow from the facts recorded "
                    "alongside it."
                ),
                evidence_refs=unique_refs([*refs, anchor_route]),
                primary_evidence_ref=anchor_route,
                source_modules=list(SOURCE_MODULES),
                why_it_matters=(
                    "The stored route and the facts it was supposedly derived "
                    "from disagree. Either the policy edition changed after the "
                    "decision, or the route was not produced by the policy at "
                    "all. Both are drift a reviewer would open on."
                ),
                regulatory_or_policy_basis=(
                    f"edd_routing_policy {stored_routing.get('policy_version')}"
                ),
                officer_question=(
                    "Was this route set by the governed policy, and under which "
                    "policy edition?"
                ),
                required_action=(
                    "Reconcile the stored routing outcome with the governed "
                    "policy result through the existing authoritative process."
                ),
                close_condition=(
                    "The stored routing outcome matches the route the governed "
                    "policy produces from the recorded fact contract."
                ),
            )
        )

    # ── Checks 1 and 2 — policy or stored route says EDD, no case ──
    if recomputed_route == ROUTE_EDD and not has_case:
        drafts.append(
            finding_draft(
                probe_id=PROBE_ID,
                probe_version=PROBE_VERSION,
                category="edd",
                severity=severity_by_reliance(bundle, approved=CRITICAL, pending=HIGH),
                status=FindingStatus.HIT,
                availability_status=AvailabilityStatus.AVAILABLE,
                confidence=1.0,
                claim=(
                    "The governed EDD policy, re-run on the fact contract "
                    f"recorded for this application, requires enhanced due "
                    f"diligence (triggers: {fired}). No EDD case exists for the "
                    "application."
                    + (
                        " The application was approved without one."
                        if approved
                        else ""
                    )
                ),
                evidence_refs=unique_refs([*refs, anchor_case]),
                primary_evidence_ref=anchor_case,
                source_modules=list(SOURCE_MODULES),
                why_it_matters=(
                    "Enhanced due diligence that policy required and the file "
                    "never received is the archetypal supervisory finding. The "
                    "EDD queue shows no case; it cannot show that one was owed."
                ),
                regulatory_or_policy_basis=(
                    f"edd_routing_policy {stored_routing.get('policy_version')}"
                ),
                officer_question=(
                    "Why did this case not proceed to enhanced due diligence when "
                    f"the policy triggers {fired} were present?"
                ),
                required_action=(
                    "Route the case to EDD through the existing authoritative "
                    "workflow, or record a governed acceptance of the divergence."
                ),
                close_condition=(
                    "An EDD case exists for the application, or the divergence is "
                    "formally documented through the existing governed process."
                ),
            )
        )
    elif stored_route == ROUTE_EDD and not has_case:
        drafts.append(
            finding_draft(
                probe_id=PROBE_ID,
                probe_version=PROBE_VERSION,
                category="edd",
                severity=severity_by_reliance(bundle, approved=CRITICAL, pending=HIGH),
                status=FindingStatus.HIT,
                availability_status=AvailabilityStatus.AVAILABLE,
                confidence=1.0,
                claim=(
                    "The stored routing outcome for this application is 'edd', "
                    "but no EDD case exists. The routing decision was recorded "
                    "and never actuated."
                ),
                evidence_refs=unique_refs([*refs, anchor_case]),
                primary_evidence_ref=anchor_case,
                source_modules=list(SOURCE_MODULES),
                why_it_matters=(
                    "The decision to escalate was taken and then lost between "
                    "the memo and the operational queue. Nothing on either "
                    "surface shows the gap, because each is internally "
                    "consistent."
                ),
                regulatory_or_policy_basis=(
                    f"edd_routing_policy {stored_routing.get('policy_version')}"
                ),
                officer_question=(
                    "What happened to the EDD case this routing decision called "
                    "for?"
                ),
                required_action=(
                    "Open the EDD case through the existing authoritative "
                    "workflow, or record why it was not required."
                ),
                close_condition=(
                    "An EDD case exists for the application, or the stored route "
                    "is corrected through the governed process."
                ),
            )
        )

    # ── Check 5 — conservative over-routing, informational ────────
    if recomputed_route == ROUTE_STANDARD and has_case:
        stages = sorted(str(case.get("stage") or "") for case in cases)
        drafts.append(
            finding_draft(
                probe_id=PROBE_ID,
                probe_version=PROBE_VERSION,
                category="edd",
                # Over-routing alongside another inconsistency warrants slightly
                # more attention than over-routing alone, but never approaches
                # under-routing: extra diligence is not a control failure.
                severity=LOW if drafts else INFO,
                status=FindingStatus.HIT,
                availability_status=AvailabilityStatus.AVAILABLE,
                confidence=1.0,
                claim=(
                    "An EDD case exists for this application (stages "
                    f"{stages}) although the governed policy, re-run on the "
                    "recorded fact contract, routes it to standard review. The "
                    "case was handled more conservatively than policy required."
                ),
                evidence_refs=unique_refs([*refs, anchor_case]),
                primary_evidence_ref=anchor_case,
                source_modules=list(SOURCE_MODULES),
                why_it_matters=(
                    "This is not a control failure — extra diligence is not a "
                    "defect. It is recorded so the divergence is explainable, and "
                    "so a pattern of routine over-routing can be seen if the "
                    "policy is mis-tuned."
                ),
                regulatory_or_policy_basis=(
                    f"edd_routing_policy {stored_routing.get('policy_version')}"
                ),
                officer_question=(
                    "Was the EDD escalation a deliberate officer judgement beyond "
                    "policy?"
                ),
                required_action=(
                    "None required. Confirm the escalation was intentional if the "
                    "pattern recurs."
                ),
                close_condition=(
                    "No action needed; the finding clears when the policy and the "
                    "route agree, or the escalation rationale is recorded."
                ),
            )
        )

    if drafts:
        return drafts

    return [
        finding_draft(
            probe_id=PROBE_ID,
            probe_version=PROBE_VERSION,
            category="edd",
            severity=INFO,
            status=FindingStatus.CLEAR,
            availability_status=AvailabilityStatus.AVAILABLE,
            confidence=1.0,
            claim=(
                "Re-running the governed EDD policy on the recorded fact "
                f"contract reproduces the stored route '{stored_route}', and the "
                "operational EDD case state is consistent with it."
            ),
            evidence_refs=unique_refs([*refs, anchor_route]),
            primary_evidence_ref=anchor_route,
            source_modules=list(SOURCE_MODULES),
            why_it_matters=(
                "Recorded so the completeness count distinguishes a routing "
                "check that ran and agreed from one that never ran."
            ),
            regulatory_or_policy_basis=(
                f"edd_routing_policy {stored_routing.get('policy_version')}"
            ),
            officer_question="None — this check passed.",
            required_action="None.",
            close_condition="Already satisfied.",
        )
    ]
