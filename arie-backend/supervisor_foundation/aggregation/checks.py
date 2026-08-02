"""The check register — one explicit table, no clustering.

Grouping, contradiction typing, missing-evidence classification and regulatory
challenges are all driven from here rather than inferred at runtime. A generic
clustering engine would be shorter and would be the wrong shape: it would
decide, per run, which findings "look similar", and a similarity judgement is
exactly the kind of thing that cannot be defended to an inspector.

**The check key.** A finding carries no ``check_id`` — adding one would change
the finding contract and therefore ``review_hash`` for every stored review, and
re-versioning the probe set is not authorised here. The key is instead derived
from the finding's ``primary_evidence_ref``, which every probe already sets to
the exact field the check examined:

    screening:{app}#subject:{key}#provider_mode   ->  "provider_mode"
    risk:{app}#factor:country_of_incorporation    ->  "factor"
    periodic_review:{id}                          ->  ""

That derivation is a rule about reference *shape*, and shapes drift. So it is
never trusted on its own: a ``(probe_id, check_key)`` pair absent from the table
below produces a **singleton group** — the finding alone, ungrouped, fully
visible. The failure mode of a stale table is less aggregation, never wrong
aggregation. ``test_the_register_covers_every_check_the_real_probes_emit`` asserts the
table covers every pair the four probes actually emit across five case shapes, so drift is caught
as a test failure rather than as silently degraded output.
"""

from __future__ import annotations

from typing import Any, Mapping, NamedTuple

# ── Missing-evidence classes ─────────────────────────────────────────
# Five distinct states, deliberately not collapsed into "missing evidence".
# They have different owners and different remedies: absent evidence is a
# client request, an unavailable check is an operations ticket, and evidence
# that is present but not defensible is neither.

EVIDENCE_ABSENT = "evidence_absent"
EVIDENCE_MALFORMED = "evidence_malformed"
EVIDENCE_NOT_DEFENSIBLE = "evidence_present_not_defensible"
CHECK_UNAVAILABLE = "check_unavailable"
EVIDENCE_NOT_REPLAYABLE = "evidence_not_replayable"

MISSING_EVIDENCE_CLASSES = (
    EVIDENCE_ABSENT,
    EVIDENCE_MALFORMED,
    EVIDENCE_NOT_DEFENSIBLE,
    CHECK_UNAVAILABLE,
    EVIDENCE_NOT_REPLAYABLE,
)

#: How an unevaluable finding is classified, by why it could not be evaluated.
#: Applied only to ``unavailable`` / ``not_replayable`` findings; a ``hit`` takes
#: its class from the register entry instead.
UNEVALUABLE_CLASS_BY_AVAILABILITY = {
    "data_absent": EVIDENCE_ABSENT,
    "snapshot_incomplete": EVIDENCE_MALFORMED,
    "credentials_absent": CHECK_UNAVAILABLE,
    "dependency_gated": CHECK_UNAVAILABLE,
    "policy_not_configured": CHECK_UNAVAILABLE,
    "available": CHECK_UNAVAILABLE,
}


# ── Contradiction taxonomy ───────────────────────────────────────────
# A contradiction is two structured facts that cannot both be right. It is not
# a synonym for "serious finding": a single missing document is a gap, not a
# contradiction. Every type below names the two facts in conflict.

CONTRADICTION_TYPES = {
    "edd_routing_divergence": (
        "The governed routing policy, replayed on the fact contract the "
        "decision path itself used, returns enhanced due diligence; the stored "
        "routing outcome is standard."
    ),
    "edd_case_absent": (
        "The stored routing outcome is enhanced due diligence; no corresponding "
        "EDD case exists in the operational record."
    ),
    "approval_on_undefensible_screening": (
        "The application was approved in reliance on a screening result that "
        "did not come from a live provider."
    ),
    "adverse_media_clear_on_absent_data": (
        "The adverse-media risk factor is scored clear; no adverse-media "
        "assessment was performed."
    ),
    "unattributed_risk_elevation": (
        "The recorded risk level was raised above the computed base level; no "
        "reason for the elevation is recorded against it."
    ),
    "material_match_closed_without_disposition": (
        "A material screening match is treated as closed; no governed "
        "disposition code is recorded against it."
    ),
}


class Check(NamedTuple):
    """One probe check, and how the review engine is allowed to present it.

    ``group_claim_template`` is used only when a group holds two or more
    findings. A single finding keeps its own claim verbatim — "not defensible
    for 1 required screening subject" is worse writing and worse evidence than
    the specific sentence the probe already wrote.
    """

    check_id: str
    anchor_noun: str
    anchor_noun_plural: str
    group_claim_template: str
    missing_evidence_class: str | None = None
    contradiction_type: str | None = None
    #: When true, the contradiction exists only where the bundle establishes
    #: that the case was approved. An undefensible screening result is a defect
    #: on any case; it is a *contradiction* only where a decision relied on it.
    #: Gated on the decision record rather than on severity — see
    #: :mod:`.decision_state` for why severity was not sufficient.
    contradiction_requires_approval: bool = False
    regulatory_challenge_template: str | None = None
    #: Used in place of ``regulatory_challenge_template`` where approval is not
    #: established. Required whenever the primary template says "approved";
    #: asserted by ``test_approval_language_has_a_neutral_alternative``.
    regulatory_challenge_template_unapproved: str | None = None


def _check(check_id: str, **kwargs: Any) -> Check:
    return Check(check_id=check_id, **kwargs)


#: Keyed by ``(probe_id, check_key)``. Every entry is a check one of the four
#: merged probes can emit. Adding a probe means adding its checks here; until
#: then its findings appear as singleton groups.
CHECK_REGISTER: Mapping[tuple[str, str], Check] = {
    # ── P-02 risk factor resolution integrity ────────────────────────
    ("P-02", "risk_config_version"): _check(
        "P-02.risk_config_version",
        anchor_noun="risk assessment",
        anchor_noun_plural="risk assessments",
        group_claim_template=(
            "The recorded risk assessment was computed under a configuration "
            "version that is not valid ({count} occurrences)."
        ),
        missing_evidence_class=EVIDENCE_NOT_DEFENSIBLE,
        regulatory_challenge_template=(
            "Under which approved risk-configuration version was this "
            "assessment computed, and how is that version evidenced?"
        ),
    ),
    ("P-02", "factor"): _check(
        "P-02.factor",
        anchor_noun="risk factor",
        anchor_noun_plural="risk factors",
        group_claim_template=(
            "The recorded risk assessment contains {count} {noun} that did not "
            "resolve to a controlled value."
        ),
        missing_evidence_class=EVIDENCE_NOT_DEFENSIBLE,
        regulatory_challenge_template=(
            "What evidence demonstrates that the unresolved {noun} did not "
            "distort the recorded risk assessment?"
        ),
    ),
    ("P-02", "elevation_reason_text"): _check(
        "P-02.elevation_reason_text",
        anchor_noun="risk elevation",
        anchor_noun_plural="risk elevations",
        group_claim_template=(
            "{count} recorded {noun} carry no attributable reason."
        ),
        missing_evidence_class=EVIDENCE_ABSENT,
        contradiction_type="unattributed_risk_elevation",
        regulatory_challenge_template=(
            "Why was this customer's risk level raised, and where is that "
            "rationale recorded?"
        ),
    ),
    ("P-02", "risk_dimensions"): _check(
        "P-02.risk_dimensions",
        anchor_noun="risk assessment",
        anchor_noun_plural="risk assessments",
        group_claim_template=(
            "The stored risk dimension record could not be read ({count} "
            "occurrences)."
        ),
        missing_evidence_class=EVIDENCE_MALFORMED,
    ),
    ("P-02", "factors"): _check(
        "P-02.factors",
        anchor_noun="risk assessment",
        anchor_noun_plural="risk assessments",
        group_claim_template=(
            "Risk factor resolution could not be evaluated ({count} "
            "occurrences)."
        ),
    ),
    # ── P-03 EDD routing divergence ──────────────────────────────────
    ("P-03", "edd_routing_facts"): _check(
        "P-03.edd_routing_facts",
        anchor_noun="routing fact contract",
        anchor_noun_plural="routing fact contracts",
        group_claim_template=(
            "The EDD routing decision cannot be replayed: the stored fact "
            "contract is absent or incomplete ({count} occurrences)."
        ),
        missing_evidence_class=EVIDENCE_MALFORMED,
        regulatory_challenge_template=(
            "On what recorded inputs was the enhanced-due-diligence routing "
            "decision made, and can that decision be reproduced?"
        ),
    ),
    ("P-03", "edd_route"): _check(
        "P-03.edd_route",
        anchor_noun="routing outcome",
        anchor_noun_plural="routing outcomes",
        group_claim_template=(
            "The stored EDD routing outcome diverges from the governed policy "
            "replayed on the same facts ({count} occurrences)."
        ),
        missing_evidence_class=EVIDENCE_NOT_DEFENSIBLE,
        contradiction_type="edd_routing_divergence",
        regulatory_challenge_template=(
            "What governed process explains routing this case to standard due "
            "diligence where the policy, on the same recorded facts, requires "
            "enhanced due diligence?"
        ),
    ),
    ("P-03", "edd_case_existence"): _check(
        "P-03.edd_case_existence",
        anchor_noun="EDD case",
        anchor_noun_plural="EDD cases",
        group_claim_template=(
            "The case is routed to enhanced due diligence with no "
            "corresponding EDD case recorded ({count} occurrences)."
        ),
        missing_evidence_class=EVIDENCE_ABSENT,
        contradiction_type="edd_case_absent",
        regulatory_challenge_template=(
            "Where is the enhanced-due-diligence case for a customer the "
            "routing policy placed in enhanced due diligence?"
        ),
    ),
    # ── P-04 screening reliance defensibility ────────────────────────
    ("P-04", "provider_mode"): _check(
        "P-04.provider_mode",
        anchor_noun="required screening subject",
        anchor_noun_plural="required screening subjects",
        group_claim_template=(
            "Screening for {count} {noun} did not come from a live provider, so "
            "those results are not defensible for reliance."
        ),
        missing_evidence_class=EVIDENCE_NOT_DEFENSIBLE,
        contradiction_type="approval_on_undefensible_screening",
        contradiction_requires_approval=True,
        regulatory_challenge_template=(
            "Why was this customer approved without a live, terminal screening "
            "result for every {noun_singular}?"
        ),
        regulatory_challenge_template_unapproved=(
            "What live, terminal screening result exists for every "
            "{noun_singular}, and when was it obtained?"
        ),
    ),
    ("P-04", "material_hit_disposition"): _check(
        "P-04.material_hit_disposition",
        anchor_noun="material screening match",
        anchor_noun_plural="material screening matches",
        group_claim_template=(
            "{count} {noun} are not defensibly dispositioned."
        ),
        missing_evidence_class=EVIDENCE_ABSENT,
        contradiction_type="material_match_closed_without_disposition",
        regulatory_challenge_template=(
            "Who reviewed the {noun} raised on this customer, under which "
            "governed disposition, and where is each decision recorded?"
        ),
    ),
    ("P-04", "screening_valid_until"): _check(
        "P-04.screening_valid_until",
        anchor_noun="screening result",
        anchor_noun_plural="screening results",
        group_claim_template=(
            "{count} {noun} for this case are outside their recorded validity "
            "period."
        ),
        missing_evidence_class=EVIDENCE_NOT_DEFENSIBLE,
        regulatory_challenge_template=(
            "What assurance does a screening result provide after its recorded "
            "validity period has expired?"
        ),
    ),
    ("P-04", "factor"): _check(
        "P-04.factor",
        anchor_noun="adverse-media factor",
        anchor_noun_plural="adverse-media factors",
        group_claim_template=(
            "The adverse-media factor is scored clear although no assessment "
            "was performed ({count} occurrences)."
        ),
        missing_evidence_class=EVIDENCE_ABSENT,
        contradiction_type="adverse_media_clear_on_absent_data",
        regulatory_challenge_template=(
            "On what adverse-media assessment does this customer's clear "
            "adverse-media score rest?"
        ),
    ),
    ("P-04", "screening_subjects"): _check(
        "P-04.screening_subjects",
        anchor_noun="screening record",
        anchor_noun_plural="screening records",
        group_claim_template=(
            "Screening reliance could not be evaluated: no per-subject "
            "screening evidence is recorded ({count} occurrences)."
        ),
        regulatory_challenge_template=(
            "Which parties were screened for this customer, and where is the "
            "per-subject record of each result?"
        ),
    ),
    ("P-04", "as_of"): _check(
        "P-04.as_of",
        anchor_noun="screening freshness check",
        anchor_noun_plural="screening freshness checks",
        group_claim_template=(
            "Screening freshness could not be evaluated ({count} occurrences)."
        ),
    ),
    # ── P-06 monitoring requirement not established ──────────────────
    ("P-06", "status"): _check(
        "P-06.status",
        anchor_noun="application",
        anchor_noun_plural="applications",
        group_claim_template=(
            "The monitoring requirement could not be evaluated ({count} "
            "occurrences)."
        ),
    ),
    ("P-06", "final_risk_level"): _check(
        "P-06.final_risk_level",
        anchor_noun="risk level",
        anchor_noun_plural="risk levels",
        group_claim_template=(
            "The recorded risk level is not one the periodic-review policy "
            "governs ({count} occurrences)."
        ),
        missing_evidence_class=EVIDENCE_NOT_DEFENSIBLE,
        regulatory_challenge_template=(
            "At what frequency is this customer reviewed, given a recorded "
            "risk level the review policy does not govern?"
        ),
    ),
    ("P-06", "periodic_reviews"): _check(
        "P-06.periodic_reviews",
        anchor_noun="periodic-review schedule",
        anchor_noun_plural="periodic-review schedules",
        group_claim_template=(
            "This customer has no valid periodic-review schedule ({count} "
            "occurrences)."
        ),
        missing_evidence_class=EVIDENCE_ABSENT,
        regulatory_challenge_template=(
            "Why was no periodic review scheduled for this approved customer "
            "at the frequency its recorded risk level requires?"
        ),
        regulatory_challenge_template_unapproved=(
            "At what frequency will this customer be reviewed, given the "
            "recorded risk level, and where is that schedule held?"
        ),
    ),
    ("P-06", "next_review_date"): _check(
        "P-06.next_review_date",
        anchor_noun="scheduled review date",
        anchor_noun_plural="scheduled review dates",
        group_claim_template=(
            "{count} {noun} fall outside the frequency the policy permits, or "
            "cannot be read."
        ),
        missing_evidence_class=EVIDENCE_NOT_DEFENSIBLE,
        regulatory_challenge_template=(
            "How does the scheduled review interval satisfy the frequency this "
            "customer's recorded risk level requires?"
        ),
    ),
    ("P-06", "policy_version"): _check(
        "P-06.policy_version",
        anchor_noun="periodic review",
        anchor_noun_plural="periodic reviews",
        group_claim_template=(
            "{count} {noun} were scheduled under a policy version that is "
            "absent or not current."
        ),
        missing_evidence_class=EVIDENCE_ABSENT,
        regulatory_challenge_template=(
            "Under which approved review-frequency policy version was this "
            "customer's review scheduled?"
        ),
    ),
    ("P-06", ""): _check(
        "P-06.review",
        anchor_noun="periodic review",
        anchor_noun_plural="periodic reviews",
        group_claim_template=(
            "{count} {noun} were assessed against the monitoring requirement."
        ),
    ),
    # (The runner's own probe-failure check is registered below the table —
    # one entry per probe, generated rather than pasted four times.)
}


#: The runner's probe-failure finding, one entry per probe. Not a compliance
#: check: it records that a probe crashed. Registered so a crash is grouped and
#: surfaced like anything else rather than falling to the unknown-key path,
#: where it would read as an ordinary singleton finding. Generated because the
#: entries differ only by probe id, and a fifth probe should not mean a fifth
#: pasted block.
CHECK_REGISTER = {
    **CHECK_REGISTER,
    **{
        (probe_id, "execution"): _check(
            f"{probe_id}.execution",
            anchor_noun="probe execution",
            anchor_noun_plural="probe executions",
            group_claim_template=(
                f"Probe {probe_id} could not run ({{count}} occurrences)."
            ),
        )
        for probe_id in ("P-02", "P-03", "P-04", "P-06")
    },
}


def check_key(primary_evidence_ref: str) -> str:
    """Derive the check key from a finding's primary evidence reference.

    The fragment after the last ``#``, truncated at the first ``:``. The
    truncation is what makes ``factor:country_of_incorporation`` and
    ``factor:monthly_volume`` the same *check* on two different factors, which
    is the grouping the officer wants: one "unresolved risk inputs" concern,
    two factors named beneath it.

    A reference with no fragment yields ``""`` — a legitimate key, registered
    for P-06, and a miss for anything else.
    """
    ref = str(primary_evidence_ref or "")
    fragment = ref.rsplit("#", 1)[-1] if "#" in ref else ""
    return fragment.split(":", 1)[0]


def lookup(probe_id: str, primary_evidence_ref: str) -> Check | None:
    """The register entry for a finding, or ``None`` for an unregistered check."""
    return CHECK_REGISTER.get((str(probe_id or ""), check_key(primary_evidence_ref)))


# ── Workflow owner, from the governed category register ──────────────
# Framework §5.2 assigns a responsible workflow owner to every category. Taking
# the owner from there rather than inventing routing keeps the review inside the
# governed taxonomy: the Supervisor names an existing role, it does not create
# one, and it never assigns a task.

OWNER_BY_CATEGORY = {
    "adverse_media": "Officer",
    "approval": "SCO",
    "audit": "MLRO",
    "business_activity": "Officer",
    "control": "Officer",
    "corporate_structure": "Officer",
    "decision": "SCO",
    "documentation": "Officer",
    "edd": "SCO",
    "expected_activity": "Officer",
    "governance": "MLRO",
    "identity": "Officer",
    "jurisdiction": "Ops",
    "legal_existence": "Officer",
    "memo": "Officer",
    "monitoring": "Officer",
    "override": "SCO",
    "ownership": "Officer",
    "pep": "Officer",
    "policy": "SCO",
    "policy_configuration": "MLRO",
    "products": "Ops",
    "registry": "Officer",
    "risk": "SCO",
    "sanctions": "SCO",
    "screening": "Officer",
    "source_of_funds": "Officer",
    "source_of_wealth": "Officer",
}

#: Used when a finding carries a category outside the governed register. The
#: review says so rather than guessing an owner.
UNKNOWN_OWNER = "unassigned"


def owner_for_category(category: str) -> str:
    return OWNER_BY_CATEGORY.get(str(category or ""), UNKNOWN_OWNER)


# ── Evidence reference types ─────────────────────────────────────────
# The prefix of a reference identifies what kind of record it points at. Listed
# explicitly so the evidence index reports a known type or an honest "other",
# never a guess parsed out of a string.

EVIDENCE_TYPE_BY_PREFIX = {
    "application": "application_field",
    "decision": "decision_record",
    "document": "document",
    "edd_case": "edd_case",
    "memo": "memo",
    "periodic_review": "periodic_review",
    "policy_profile": "policy_configuration",
    "risk": "risk_factor",
    "screening": "screening_evidence",
    "screening_review": "screening_review",
    "supervisor_probe": "probe_execution",
    "supervisor_verdict": "supervisor_verdict",
}

UNKNOWN_EVIDENCE_TYPE = "other"


def evidence_type(reference: str) -> str:
    prefix = str(reference or "").split(":", 1)[0].split("#", 1)[0]
    return EVIDENCE_TYPE_BY_PREFIX.get(prefix, UNKNOWN_EVIDENCE_TYPE)
